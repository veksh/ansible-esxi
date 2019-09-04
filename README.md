
Ansible has some great modules for VMware vCenter (especially in 2.5), but none for
managing standalone ESXi hosts. There are many cases when full vCenter infrastructure
is not required and web-based Host UI is quite enough for routine administrative tasks.

Modules, roles and playbooks presented here allow to manage standalone ESXi hosts
(although hosts under vCenter are ok too) with direct SSH connection, usually with
transparent key-based authentication.

# Contents of repository

- role to configure ESXi host (`roles/hostconf_esxi`)
- playbooks to deploy new VMs to ESXi host (in `vm_deploy/`)
    - by uploading (template) VM from some other host (`upload_clone`)
    - or by cloning local VM (`clone_local`)
- modules used by role and deployment playbook
    - to gather VM facts from ESXi host (`esxi_vm_info`)
    - to manage autostart of VMs (`esxi_autostart`)
    - to install or update custom VIBs (`esxi_vib`)
- some helper filter plugins to simplify working with ESXi shell commands output
    - `split`: split string into a list
    - `todict`: convert a list of records into a dictionary, using specified field as a key
- example playbook to update ESXi host with offline bundle (`update_esxi.yaml`)
- helper script to get vault pass from macOS keychain (`get_vault_pass.esxi.sh`)

# `hostconf-esxi` role

This role takes care of many aspects of standalone ESXi server configuration like

- ESXi license key (if set)
- host name, DNS servers
- NTP servers, enable NTP client, set time
- users
    - create missed, remove extra ones
    - assign random passwords to new users (and store in `creds/`)
    - make SSH keys persist across reboots
    - grant DCUI rights
- portgroups
    - create missed, remove extra
    - assign specified tags
- block BPDUs from guests
- create vMotion interface (off by default, see `create_vmotion_iface` in role defaults)
- datastores
    - partition specified devices if required
    - create missed datastores
    - rename empty ones with wrong names
- autostart for specified VMs (optionally disabling it for all others)
- logging to syslog server; lower `vpxa` and other noisy components logging level from
  default `verbose` to `info`
- certificates for Host UI and SSL communication (if present)
- install or update specified VIBs

Only requirement is correctly configured network (especially uplinks) and reachability
over ssh with root password. ESXi must be reasonably recent (6.0+, although some
newer versions of 5.5 have working python 2.7 too).

## General configuration
- `ansible.cfg`: specify remote user, inventory path etc; specify vault pass method
  if using one for certificate private key encryption.
- `group_vars/all.yaml`: specify global parameters like NTP and syslog servers there
- `group_vars/<site>.yaml`: set specific params for each `<site>` in inventory
- `host_vars/<host>.yaml`: override global and group values with e.g. host-specific
  users list or datastore config
- put public keys for users into `roles/hostconf-esxi/files/id_rsa.<user>@<keyname>.pub`
  for referencing them later in user list `host_vars` or `group_vars`

## Typical variables for `(group|host)_vars`
- serial number to assign, usually set in global `group_vars/all.yaml`; does not get
  changed if not set

        esxi_serial: "XXXXX-XXXXX-XXXX-XXXXX-XXXXX"

- general network environment, usually set in `group_vars/<site>.yaml`

        dns_domain: "m0.maxidom.ru"

        name_servers:
          - 10.0.128.1
          - 10.0.128.2

        ntp_servers:
          - 10.1.131.1
          - 10.1.131.2

        # defaults: "log." + dns_domain
        # syslog_host: log.m0.maxidom.ru

- user configuration: those users are created (if not present) and assigned random
  passwords (printed out and stored in `creds/<user>.<host>.pass.out`), have ssh keys assigned to them (persistently) and restricted to specified hosts (plus global list
  in `permit_ssh_from`), are granted administrative rights and access to the console

        esxi_local_users:
        "<user>":
          desc: "<user description>""
          pubkeys:
            - name:  "<keyname>"
              hosts: "1.2.3.4,some-host.com"

    users that are not in this list (except root) are removed from host, so be careful.
- network configuration: portgroups list in `esxi_portgroups` are exhaustive, i.e. those
  and only those portgroups (with exactly matched tags) should be present oh host after
  playbook run (missed are created, wrong names are fixed, extra are removed if not used)

        esxi_portgroups:
          all-tagged: { tag: 4095 }
          adm-srv:    { tag:  210 }
          srv-netinf: { tag:  131 }
          pvt-netinf: { tag:  199 }
          # could also specify vSwitch (default is vSwitch0)
          adm-stor:   { tag:   21, vswitch: vSwitch1 }

- datastore configuration: datastores would be created on those devices if missed and
  `create_datastores` is set; existent datastores would be renamed to match specified
  name if `rename_datastores` is set and they are empty

        local_datastores:
          "vmhba0:C0:T0:L1": "nest-test-sys"
          "vmhba0:C0:T0:L2": "nest-test-apps"

- VIBs to install or update (like latest esx-ui host client fling)

        vib_list:
          - name: esx-ui
            url: "http://www-distr.m1.maxidom.ru/suse_distr/iso/esxui-signed-6360286.vib"

- autostart configuration: listed VMs are added to esxi auto-start list, in specified order
  if order is present, else just randomly; if `autostart_only_listed` is set, only those VMs
  will be autostarted on host with extra VMs removed from autostart

        vms_to_autostart:
          eagle-m0:
            order: 1
          hawk-m0:
            order: 2
          falcon-u1:

## Host-specific configuration
- add host into corresponding group in `inventory.esxi`
- set custom certificate for host
    - put certificate into `files/<host>.rui.crt`,
    - put key into `files/<host>.key.vault` (and encrypt vault)
- override any group vars in `host_vars/hostname.yaml`

## Initial host setup and later convergence runs

For the initial config only the "root" user is available, so run playbook like this:

      ansible-playbook all.yaml -l new-host -u root -k --tags hostconf --diff

After local users are configured (and ssh key auth is in place), just use `remote_user`
from `ansible.cfg` and run it like

      ansible-playbook all.yaml -l host-or-group --tags hostconf --diff

## Notes
- only one vSwitch (`vSwitch0`) is currently supported
- password policy checks (introduced in 6.5) are turned off to allow for truly random
  passwords (those are sometimes miss one of the character classes).

# VM deployment playbooks

There are two playbooks in `vm_deploy/` subdir

- first (`upload_clone`) is for copying template VM from source host to new target
- second (`clone_local`) is for making custom clones of local template VM

See playbook source and comments at the top for a list if parameters, some are
mentioned below.

## Assumptions about environment

- ansible 2.3+ (2.2 "replace" is not compatible with python 3 on ESXi)
- local modules `netaddr` and `dnspython`
- clone source must be powered off
- for VM customization like setting IPs etc, [ovfconf](https://github.com/veksh/ovfconf)
  must be configured on clone source VM (to take advantage of passing OVF params to VM)

## `upload_clone`

This playbooks is mostly used to upload initial "template" VM to target host (to be,
in turn, template for further local cloning). Source of template VM is usually at
another ESXi host, and there are 3 modes of copy:

- direct "pull" SCP: destination host is SCP'ing VM files from source; authorization
  is key-based with agent forwarding, so both hosts must have current Ansible user
  configured and destination host must be in allowed hosts list for this user
- direct "push" SCP: source host is SCP'ing VM files to destination, exactly as above
  (if e.g. firewall is more permissive in that direction)
- slow copy via current hosts: download VM files from source to temp dir first (with
  Ansible "copy" module; rather fast if file is already staged there), then upload it
  to destination hosts (must have enough space in "tmp" for that, see `ansible-deploy.cfg`
  for tmp configuration)

There are no options for customization there, only for src and dst params like datastore,
and usual invocation looks like

      ansible-playbook upload_clone.yaml -l nest2-k1 \
        -e 'src_vm_name=phoenix11-1-k1 src_vm_vol=nest1-sys src_vm_server=nest1-k1' \
        -e 'dst_vm_name=phoenix11-2-k1' \
        -e 'direct_scp=true'

## `clone_local`

This playbook is used to produce new VM from local template source, optionally customize
parameters like datastore, network and disks, and optionally power it on. Invocation
to create new machine (with additional network card and disk) and power it on looks like

    ansible-playbook clone_local.yaml -l nest1-mf1 -e 'vm_name=files-mf1-vm \
      vm_desc="samba file server" vm_net2=srv-smb vm_disk2=100G' \
      -e 'do_power_on=true'

To simplify cloning, it is better to

- specify local clone source vm in ESXi host `host_vars` (as `src_vm_name`)
- already have new machine's name in DNS (so IP is determined automatically)
- have [ovfconf](https://github.com/veksh/ovfconf) configured in source (template)
  VM, as OVF is used to pass network config there (DHCP server would be ok too)

# Modules

Modules (`library/`) are documented with usual Ansible docs. They could be used
stand-alone, like

      ansible -m esxi_vm_list -a 'get_power_state=true get_start_state=true' esxi-name

to get a list of host VMs together with autostart state and current run state
