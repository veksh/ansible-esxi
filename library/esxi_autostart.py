#!/usr/bin/python
'''
source ~/tmp/ansible/hacking/env-setup
export PATH=$PATH:~/tmp/ansible/hacking/
test-module -m esxi_autostart.py -a 'name=eagle-m8 start=yes mock=yes'

export ANSIBLE_CONFIG=~/works/sysadm/ansible-study/esxi-mgmt/ansible.esxi.cfg
ansible -m esxi_autostart -a 'name=eagle-m8' nest1-m8
'''

from ansible.module_utils.basic import AnsibleModule
import re

ANSIBLE_METADATA = {'status': ['preview'],
                    'supported_by': 'committer',
                    'version': '0.1'}

DOCUMENTATION = '''
---
module: esx_autostart
short_description: manages VM startup for stand-alone ESXi host
version_added: "2.2"
description:
    - 'This module manages VM startup on ESXi with ssh and C("vim-cmd").'
    - 'It allows to enable or disable autostart for named VM and optionally specify
       startup order.'
options:
    name:
        description: 'Name of registered VM to manage'
        required: true
        aliases: ["vm"]
    enabled:
        description: 'Whether VM should be started at host starup'
        default: true
        aliases: ["autostart"]
    order:
        description: 'Relative startup order. If some VM already occupy this place, it is shifted
            down along with rest of VMs with higher startup order. List is always sequential
            and VMs are numbered from 1. By default, VMs are added at the end of startup list'
        required: false
    skip:
        description: 'Skip bad/not-yet-registered VM names without error (by default, it is error).
            Better check them in host facts if available :)'
        default: False
    state:
        description: 'Whether VM should be running now, default: do not change state'
        required: false
        choices: ["started", "stopped"]
author: alex@maxidom.ru
notes:
    - 'works w/o vcenter via C(ssh)'
    - 'Note that ESXi autostart manager API is rather buggy:'
    - 'There is no clear way to disable VM startup (module is setting start action to
      "PowerOff")'
    - 'Moving VM startup order around is OK as long as new order != end of startup
      list, in that case list is corrupted'
    - 'Setting order to wrong number (0, 999, etc) removes VM from command output
      but entry is still in C(/etc/vmware/hostd/vmAutoStart.xml); later actions could
      result in duplicate sequence numbers with unknown consequences'
requirements: []

'''


EXAMPLES = '''

# make eagle-m8 autostart at first position
- esxi_autostart:
  name: eagle-m8
  start: yes
  order: 1

# disable phoenix11 autostart
- esxi_autostart:
  name: phoenix11
  enabled: false

'''

# either cat mock files (for test) or run actual cmds (for real)
MOCK_DIR = '/Users/alex/works/sysadm/ansible-study/esxi-mgmt/experiments/mocks'
COMMANDS = {
    'real': {
        'get_vmlist': 'vim-cmd vmsvc/getallvms',
        'get_autoruns': 'vim-cmd hostsvc/autostartmanager/get_autostartseq',
        'mod_start': 'vim-cmd hostsvc/autostartmanager/update_autostartentry ' +
                     '{vm_id} "PowerOn" "10" "{order}" ' +
                     '"guestShutdown" "systemDefault" "systemDefault"',
        # use '--' to mark end of options or else it will complain about -1
        'disable_start': 'vim-cmd hostsvc/autostartmanager/update_autostartentry -- ' +
                         '"{vm_id}" "PowerOff" "1" "-1" ' +
                         '"guestShutdown" "systemDefault" "systemDefault"'
    },
    'mock': {
        'get_vmlist': 'cat %s/getallvms.txt' % MOCK_DIR,
        'get_autoruns': 'cat %s/get_autostartseq.txt' % MOCK_DIR,
        'mod_start': 'echo "set {vm_id} to start at {order}"',
        'disable_start': 'echo "disable {vm_id} startup at {order}"'
    },
}


class VMStartMgr(object):
    """ manager for autostart entries """

    def __init__(self, module):
        self.module = module
        self.params = self.module.params

        self.check_mode = module.check_mode
        self.mock = module.params['mock']
        if self.mock:
            self.commands = COMMANDS['mock']
        else:
            self.commands = COMMANDS['real']
        self.vmname_to_id = self.load_vm_list()
        self.vm_start_info = self.load_startup_list()

    def load_vm_list(self):
        ''' construct map "vm_name -> vm_id" from file or program '''
        vmlist = dict()
        ret, out, err = self.module.run_command(self.commands['get_vmlist'])
        if ret != 0:
            self.module.fail_json(msg="unable to get vm list", rc=ret, err=err)
        for line in out.split('\n'):
            if line.startswith('Vmid') or line == '':
                continue
            # multiline annotations are tricky
            if not re.match(r'^\d+ +\S+ +\[\S+\] \S+/\S+\.vmx', line):
                continue
            lfields = line.split()
            vmlist[lfields[1]] = int(lfields[0])
        return vmlist

    def load_startup_list(self):
        '''
        construct map "vm_id -> {autorun properties}" from command output
        currently we are interested in
          - "order": autostart order, int 1..N
          - "action": startAction from list; string, could be
            - "PowerOn": default
            - "PowerOff": one known way to disable autostart
                - DirectUI fling sets startOrder = -1 to disable autostart
                - lets use both to make sure :)
        '''
        sinfo = dict()
        vm_id = 0
        # for line in file(STARTUP_FILE):
        # or subprocess.Popen + res.stdout.readlines
        # for line in os.popen("cat %s" % STARTUP_FILE).readlines():
        ret, out, err = self.module.run_command(self.commands['get_autoruns'])
        if ret != 0:
            self.module.fail_json(msg="unable go get startup list", rc=ret, err=err)
        if out == '(vim.host.AutoStartManager.AutoPowerInfo) []':
            return sinfo
        for line in out.split('\n'):
            if line.lstrip().startswith(('(', '}', ']')) or line == '':
                continue
            (key, _, val) = line.strip("', \n").strip().split()
            if key == 'key':
                # key = 'vim.VirtualMachine:3',
                vm_id = int(val.split(":")[1])
                sinfo[vm_id] = {}
            elif key == 'startOrder':
                sinfo[vm_id]['order'] = int(val)
            elif key == 'startAction':
                sinfo[vm_id]['action'] = val.strip('"')
        return sinfo

    def update_vm(self):
        '''
        Perform actual autostart db update
        - adds vm to autostart manager db if not yet
        - changes order if specified
        - there is no clear way to remove VM, so disable it with startup action set to PowerOff
        '''
        vm_name = self.params['name']
        new_start = self.params['enabled']
        new_order = self.params['order']

        if vm_name not in self.vmname_to_id:
            if self.params['skip']:
                return (False, "VM %s not found, skipping" % vm_name, {})
            else:
                self.module.fail_json(msg="no such vm here: %s" % vm_name, rc=-1)

        vm_id = self.vmname_to_id[vm_name]
        start_cmd = self.commands['mod_start']
        disable_cmd = self.commands['disable_start']

        # note module.check_mode and mock
        command = None
        changed = False
        ret_msg = 'all ok'
        ret_params = {'vm_id': vm_id}
        if not new_start:
            if vm_id in self.vm_start_info:
                old_order = self.vm_start_info[vm_id]['order']
                old_action = self.vm_start_info[vm_id]['action']
                if old_action != "PowerOff":
                    changed = True
                    ret_msg = "autostart disabled, moved to pos -1"
                    command = disable_cmd.format(vm_id = vm_id)
                    ret_params['old_action'] = old_action
                else:
                    ret_msg = "already ok: autostart disabled"
            else:
                ret_msg = "already ok: not in autostart"
        else:
            if vm_id in self.vm_start_info:
                old_order = self.vm_start_info[vm_id]['order']
                old_action = self.vm_start_info[vm_id]['action']
                if old_action != "PowerOn" or old_order == -1:
                    changed = True
                    if new_order is None:
                        new_order = len([v for v in self.vm_start_info.values() if v['order'] > 0]) + 1
                    ret_msg = "autostart enabled at pos %d" % new_order
                    ret_params['old_action'] = old_action
                    ret_params['old_pos'] = old_order
                    ret_params['new_pos'] = new_order
                    command = start_cmd.format(vm_id = vm_id, order = new_order)
                if new_order is not None and new_order != old_order and not changed:
                    changed = True
                    command = start_cmd.format(vm_id = vm_id, order = new_order)
                    ret_msg = "autostart enabled, moved from %d to %d" % (old_order, new_order)
                    ret_params['old_pos'] = old_order
                    ret_params['new_pos'] = new_order
                if not changed:
                    ret_msg = "already ok: autostart enabled, pos %d" % old_order
            else:
                changed = True
                if new_order is None:
                    new_order = len([v for v in self.vm_start_info.values() if v['order'] > 0]) + 1
                command = start_cmd.format(vm_id = vm_id, order = new_order)
                ret_msg = "autostart added at pos %d" % new_order
                ret_params['new_pos'] = new_order

        if command is not None:
            ret_params['command'] = command
            if not self.check_mode:
                ret, out, err = self.module.run_command(command)
                if ret != 0:
                    self.module.fail_json(msg="unable to perform changes",
                                          cmd=command, rc=ret, err=err)
                ret_params['cmd_ret'] = ret
                ret_params['cmd_out'] = out
                ret_params['cmd_err'] = err
        return (changed, ret_msg, ret_params)


def main():
    ''' entry point, simple one for now
        run with test-module -m esxi_autostart.py -a "name=hren"
    '''
    module = AnsibleModule(
        argument_spec = dict(
            name = dict(aliases=['vm'], required=True),
            enabled = dict(aliases=['autostart'], required=False, type='bool', default=True),
            order = dict(required=False, type='int'),
            state = dict(required=False, type='str',
                       choices=["started", "stopped"]),
            mock = dict(required=False, type='bool', default=False),
            skip = dict(required=False, type='bool', default=False)
        ),
        supports_check_mode=True,
        required_one_of=[['enabled', 'state']],
    )
    # module.debug('stated')
    mgr = VMStartMgr(module)
    changed, msg, params = mgr.update_vm()
    module.exit_json(changed=changed, msg=msg, **params)


if __name__ == '__main__':
    main()
