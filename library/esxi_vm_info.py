#!/usr/bin/python
'''
source ~/tmp/ansible/hacking/env-setup
export PATH=$PATH:~/tmp/ansible/hacking/
test-module -m esxi_vm_list.py -a 'mock=yes'

export ANSIBLE_CONFIG=~/works/sysadm/ansible-study/esxi-mgmt/ansible.esxi.cfg
ansible -m esxi_vm_list nest1-m8
'''

from ansible.module_utils.basic import AnsibleModule
import re

ANSIBLE_METADATA = {'status': ['preview'],
                    'supported_by': 'committer',
                    'version': '0.1'}

DOCUMENTATION = '''
---
module: esx_vm_info
short_description: list registered VMs and their properties for stand-alone ESXi host
version_added: "2.2"
description:
    - 'This module lists VMs on ESXi hosts, returing serveral dicts.'
    - 'Basic info include C(vm_by_id) (maps VM id => VM name) and C(id_by_vm)
      (maps VM name => VM id (int)).'
    - 'Optional are C(start_by_vm) (VM name => autostart sequence number, absent
      if VM is not started automatically) and C(power_by_vm) (VM name => current power
      state, C(true) if powered on).'
options:
    get_start_state:
        description: include C(start_by_vm) dictionary with startup sequence
        default: False
    get_power_state:
        description:
            - include C(power_by_vm) dictionary with power state.
            - C(true) means VM is currently powered on.
            - Takes longer to calculate.
        default: False
requirements: []
author: alex@maxidom.ru
notes:
    - works w/o vcenter via C(ssh)
    - VM id is string as C(vm_by_id) key because C(int) could not be a key in JSON
'''

EXAMPLES = '''

# get basic vm info
- name: get vm info
  esxi_vm_info:
  register: vminfo

# use it to find if VM is registered
- name: fix autostart for registered VMs
  esxi_autostart:
    name:  "{{ item.name }}"
    start: "{{ item.start }}"
    order: "{{ item.order | default(omit) }}"
  with_items: "{{ vms_to_start }}"
  when: item.name in vminfo.id_by_vm
'''

MOCK_DIR = '/Users/alex/works/sysadm/ansible-study/esxi-mgmt/experiments/mocks'


def load_vm_list(module):
    ''' construct map "vm_name -> vm_id" from file or program '''
    id_by_vm = dict()
    vm_by_id = dict()
    path_by_vm = dict()
    # ret, out, err = module.run_command('cat %s/getallvms.txt' % MOCK_DIR)
    ret, out, err = module.run_command('vim-cmd vmsvc/getallvms')
    if ret != 0:
        module.fail_json(msg="unable to get vm list", rc=ret, err=err)
    for line in out.split('\n'):
        if line.startswith('Vmid') or line == '':
            continue
        # multiline annotations are tricky
        lparts = re.match(r'^(?P<id>\d+) +(?P<name>\S+) +\[(?P<store>\S+)\] (?P<path>\S+)/(?P<file>\S+)\.vmx ', line)
        if not lparts:
            continue
        id_by_vm[lparts.group("name")] = int(lparts.group("id"))
        vm_by_id[lparts.group("id")] = lparts.group("name")
        path_by_vm[lparts.group("name")] = "/vmfs/volumes/" + lparts.group("store") + "/" + lparts.group("path") + "/" + lparts.group("file") + ".vmx"
    return vm_by_id, id_by_vm, path_by_vm


def load_startup_list(module, vm_by_id):
    '''
    construct map "vm_name -> autostart_order" for autostart
    if machine is not in list
    '''
    sinfo = dict()
    vm_name = ''
    vm_enabled = False
    vm_order = 0
    # for line in file(STARTUP_FILE):
    # or subprocess.Popen + res.stdout.readlines
    # for line in os.popen("cat %s" % STARTUP_FILE).readlines():
    ret, out, err = module.run_command('vim-cmd hostsvc/autostartmanager/get_autostartseq')
    if ret != 0:
        module.fail_json(msg="unable go get startup list", rc=ret, err=err)
    for line in out.split('\n'):
        if line.lstrip().startswith(('(', '}', ']')) or line == '':
            continue
        (key, _, val) = line.strip("', \n").strip().split()
        if key == 'key':
            # key = 'vim.VirtualMachine:3',
            vm_name = vm_by_id[val.split(":")[1]]
            vm_enabled = False
            vm_order = 0
        elif key == 'startOrder':
            vm_order = int(val)
            if vm_enabled:
                sinfo[vm_name] = vm_order
            #sinfo[vm_name] = vm_order
        elif key == 'startAction':
            # could be 'PowerOn' and 'powerOn'
            if str.lower(val.strip('"')) == 'poweron':
                vm_enabled = True
                if vm_order > 0:
                    sinfo[vm_name] = vm_order
            else:
                vm_enabled = False
    return sinfo


def load_power_list(module, vm_by_id):
    '''
    make map "vm_name -> power_state
    '''
    pinfo = dict()
    for (vm_id, vm_name) in vm_by_id.items():
        ret, out, err = module.run_command('vim-cmd vmsvc/power.getstate %s' % vm_id)
        if out.endswith("on\n"):
            pinfo[vm_name] = True
        else:
            pinfo[vm_name] = False
    return pinfo

def main():
    ''' entry point, simple one for now
        run mock: test-module -m esxi_vm_list.py
        run real: ansible     -m esxi_vm_list nest1-m8
    '''
    module = AnsibleModule(
        argument_spec = dict(
            get_start_state = dict(required=False, type='bool', default=False),
            get_power_state = dict(required=False, type='bool', default=False),
            ),
        supports_check_mode=True,
    )
    # module.debug('stated')
    # mgr = VMStartMgr(module)
    ret_dict = dict()
    vm_by_id, id_by_vm, path_by_vm = load_vm_list(module)
    ret_dict['vm_by_id'] = vm_by_id
    ret_dict['id_by_vm'] = id_by_vm
    ret_dict['path_by_vm'] = path_by_vm
    if module.params['get_start_state']:
        ret_dict['start_by_vm'] = load_startup_list(module, vm_by_id)
    if module.params['get_power_state']:
        ret_dict['power_by_vm'] = load_power_list(module, vm_by_id)
    module.exit_json(changed = False, **ret_dict)

if __name__ == '__main__':
    main()
