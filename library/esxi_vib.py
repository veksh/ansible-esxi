#!/usr/bin/python
'''
source ~/tmp/ansible/hacking/env-setup
export PATH=$PATH:~/tmp/ansible/hacking/
test-module -m esxi_vib.py -a 'mock=yes name=esx-ui state=present'

export ANSIBLE_CONFIG=~/works/sysadm/ansible-study/esxi-mgmt/ansible.esxi.cfg
ansible -m esxi_vib 'name=esx-ui state=present' --check nest1-m8
'''

from ansible.module_utils.basic import AnsibleModule

ANSIBLE_METADATA = {'status': ['preview'],
                    'supported_by': 'committer',
                    'version': '0.1'}

DOCUMENTATION = '''
---
module: esxi_vib
short_description: manage VIB package installation on ESXi
version_added: "2.2"
description:
    - Manages installation, update and deinstallation of VIB packages on ESXi hosts.
options:
    name:
        description: VIB package name
        required: true
    url:
        description: http url to package to install or update
    state:
        description:
          - C(present) will make sure the package is installed.
            C(latest)  will make sure the latest version of the package is installed.
            C(absent)  will make sure the specified package is not installed.
        required: false
        choices: [ present, latest, absent ]
        default: "present"
author: alex@maxidom.ru
notes:
    - works w/o vcenter via C(ssh)
requirements:
    - none
'''

EXAMPLES = '''
# install ESXi Embedded Host Client
- name: inst host client
  esxi_vib:
    name: esx-ui
    url:  http://distr.internal/vibs/esxui-signed-4974903.vib

'''


def parse_cmd_responce(lines, skip_empty = True):
    ''' parse multiline responce from "esxcli software vib", looking like

              VMware_bootbank_esx-ui_0.0.2-0.1.3172496
                 Name: esx-ui
                 Version: 0.0.2-0.1.3172496

        and extracts interesting attrs, mapping name to shorter version
        mb using "esxcli --formatter=xml" would be wiser :)
    '''
    res = dict()
    title = None
    for line in lines.split('\n'):
        if title is None:
            title = line
        elif line.startswith('   '):
            key, val = line.lstrip().split(":", 1)
            vals = val.lstrip()
            if not (skip_empty and vals == ''):
                res[key] = val.lstrip()

    res['Title'] = title
    return res


def get_vib_state(module, vib_name, skip_empty = True):
    ''' gets current state of VIB (installed or not) and version if installed'''
    # also: 'esxcli software profile get' to view installed
    ret, out, err = module.run_command("esxcli software vib get -n %s" % vib_name)
    if ret != 0:
        if out.lstrip().startswith('[NoMatchError]'):
            return "absent", None
        module.fail_json(msg="unable to get vib info", rc=ret, err=err, out=out)
    details = parse_cmd_responce(out, skip_empty)
    if 'Version' in details:
        return "present", details
    else:
        module.fail_json(msg="package %s is neither present nor absent" % vib_name, out=out, err=err)


def main():
    ''' entry point, simple one for now
        run with test-module -m esxi_vib.py -a "name=hren"
    '''
    module = AnsibleModule(
        argument_spec = dict(
            name = dict(required=True),
            state = dict(required=False, default='present', choices=['present', 'latest', 'absent']),
            url = dict(required=False)
        ),
        supports_check_mode=True,
    )
    vib_name = module.params['name']
    vib_url = module.params['url']
    state_new = module.params['state']
    state_curr, details_curr = get_vib_state(module, vib_name)
    command = None
    action = None
    if state_new == 'absent':
        if state_curr != 'absent':
            command = "esxcli software vib remove -n {0}".format(vib_name)
            action = 'remove'
    elif state_new == 'present':
        if state_curr != 'present':
            command = "esxcli software vib install -v {0}".format(vib_url)
            action = 'install'
    elif state_new == 'latest':
        if state_curr == 'present':
            command = "esxcli software vib update -v {0}".format(vib_url)
            action = 'update'
        else:
            command = "esxcli software vib install -v {0}".format(vib_url)
            action = 'install'
    else:
        module.fail_json(msg="unknown new state %s" % state_new)

    if action is None:
        module.exit_json(changed=False, msg="already ok: %s" % state_curr, details = details_curr)

    full_cmd = command + (" --dry-run" if module.check_mode else '')
    ret, out, err = module.run_command(full_cmd)
    if ret != 0:
        # "vib update" sometimes fail with empty message, but actual result is ok
        if action == 'update' and ret == 1 and err == "" and out == "''\n":
            ret2, out2, err2 = module.run_command(full_cmd)
            state_new, details_new = get_vib_state(module, vib_name)
            if details_curr['Version'] != details_new['Version']:
                module.exit_json(changed = True, msg="update finished mostly ok :)")
            else:
                module.exit_json(changed = False, msg="update skipped mostly ok :)")
        else:
            module.fail_json(msg="command failed", cmd=full_cmd, rc=ret, err=err, out=out)
    res_details = parse_cmd_responce(out)
    changed = ('VIBs Installed' in res_details or 'VIBs Removed' in res_details)
    module.exit_json(changed=changed, command=full_cmd, details=res_details)

if __name__ == '__main__':
    main()
