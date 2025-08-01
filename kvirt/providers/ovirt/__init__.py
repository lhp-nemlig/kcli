# -*- coding: utf-8 -*-

from kvirt import common
from kvirt.common import error, pprint, warning, get_ssh_pub_key
from kvirt.defaults import UBUNTUS, METADATA_FIELDS
import ovirtsdk4 as sdk
from ovirtsdk4 import Error as oerror
import ovirtsdk4.types as types
import os
import sys
from subprocess import call, check_output
import json
from time import sleep, time
import yaml
from http.client import HTTPSConnection
import ssl
from urllib.parse import urlparse
from random import choice
from string import ascii_lowercase
from shutil import which

oimages = {'CentOS-6-x86_64-GenericCloud.qcow2': 'CentOS 6 Generic Cloud Image',
           'CentOS-Atomic-Host-7-GenericCloud.qcow2': 'CentOS 7 Atomic Host Image',
           'CentOS-7-x86_64-GenericCloud.qcow2': 'CentOS 7 Generic Cloud Image',
           'cirros-0.4.0-x86_64-disk.img': 'CirrOS 0.4.0 for x86_64',
           'Fedora-Cloud-Base-24-1.2.x86_64.qcow2': 'Fedora 24 Cloud Base Image v20160921.0 for x86_64',
           'Fedora-Cloud-Base-25-1.3.x86_64.qcow2': 'Fedora 25 Cloud Base Image v20170106.0 for x86_64',
           'Fedora-Cloud-Base-26-1.5.x86_64.qcow2': 'Fedora 26 Cloud Base Image v1.5 for x86_64',
           'Fedora-Cloud-Base-27-1.6.x86_64.qcow2': 'Fedora 27 Cloud Base Image v1.6 for x86_64',
           'Fedora-Cloud-Base-28-1.1.x86_64.qcow2': 'Fedora 28 Cloud Base Image v1.1 for x86_64',
           'trusty-server-cloudimg-amd64-disk1.img':
           'Ubuntu Server 14.04 LTS (Trusty Tahr) Cloud Image v20170110 for x86_64',
           'xenial-server-cloudimg-amd64-disk1.img':
           'Ubuntu Server 16.04 LTS (Xenial Xerus) Cloud Image v20170111 for x86_64',
           'yakkety-server-cloudimg-amd64-disk1.img':
           'Ubuntu Server 16.10 (Yakkety Yak) Cloud Image v20170106 for x86_64'}


class KOvirt(object):
    def __init__(self, host='127.0.0.1', port=22, user='admin@internal',
                 password=None, insecure=True, ca_file=None, org=None, debug=False,
                 cluster='Default', datacenter='Default', ssh_user='root', filtervms=False, filteruser=False,
                 filtertag=None):
        try:
            url = f"https://{host}/ovirt-engine/api"
            self.conn = sdk.Connection(url=url, username=user,
                                       password=password, insecure=insecure,
                                       ca_file=ca_file)
        except oerror as e:
            error(f"Unexpected error: {e}")
            return None
        self.debug = debug
        self.vms_service = self.conn.system_service().vms_service()
        self.templates_service = self.conn.system_service().templates_service()
        self.sds_service = self.conn.system_service().storage_domains_service()
        self.datacenter = datacenter
        self.cluster = cluster
        self.host = host
        self.port = port
        self.user = user
        self.ca_file = ca_file
        self.org = org
        self.ssh_user = ssh_user
        self.filtervms = filtervms
        self.filteruser = filteruser
        self.filtertag = filtertag
        self.netprofiles = {}

    def close(self):
        self.api.disconnect()
        return

    def exists(self, name):
        vmsearch = self.vms_service.list(search=f'name={name}')
        if vmsearch:
            return True
        return False

    def net_exists(self, name):
        profiles_service = self.conn.system_service().vnic_profiles_service()
        if not self.netprofiles:
            for prof in profiles_service.list():
                networkinfo = self.conn.follow_link(prof.network)
                netdatacenter = self.conn.follow_link(networkinfo.data_center)
                if netdatacenter.name == self.datacenter:
                    self.netprofiles[prof.name] = prof.id
            if 'default' not in self.netprofiles:
                if 'ovirtmgmt' in self.netprofiles:
                    self.netprofiles['default'] = self.netprofiles['ovirtmgmt']
                elif 'rhevm' in self.netprofiles:
                    self.netprofiles['default'] = self.netprofiles['rhevm']
        if name in self.netprofiles:
            return True
        return False

    def disk_exists(self, pool, name):
        print("not implemented")

    def create(self, name, virttype=None, profile='', flavor=None, plan='kvirt',
               cpumodel='host-model', cpuflags=[], cpupinning=[], numcpus=2, memory=512,
               guestid='guestrhel764', pool='default', image=None,
               disks=[{'size': 10}], disksize=10, diskthin=True,
               diskinterface='virtio', nets=['default'], iso=None, vnc=True,
               cloudinit=True, reserveip=False, reservedns=False,
               reservehost=False, start=True, keys=[], cmds=[], ips=None,
               netmasks=None, gateway=None, nested=True, dns=None, domain=None,
               tunnel=False, files=[], enableroot=True, alias=[], overrides={},
               tags=[], storemetadata=False, sharedfolders=[], cmdline=None, placement=[], autostart=False,
               cpuhotplug=False, memoryhotplug=False, numamode=None, numa=[], pcidevices=[], tpm=False, rng=False,
               metadata={}, securitygroups=[], vmuser=None, guestagent=True):
        ip = None
        initialization = None
        memory = memory * 1024 * 1024
        clone = not diskthin
        custom_properties = []
        ignition = False
        if image is not None:
            templates_service = self.templates_service
            templateslist = templates_service.list()
            found = False
            for temp in templateslist:
                if temp.name == image:
                    if temp.memory > memory:
                        memory = temp.memory
                        pprint(f"Using {memory}Mb for memory as defined in template {image}")
                    _template = types.Template(name=image)
                    found = True
            if not found:
                return {'result': 'failure', 'reason': f"image {image} not found"}
            if image is not None and common.needs_ignition(image):
                ignition = True
                cloudinit = False
                ignitiondata = ''
                version = common.ignition_version(image)
                ignitiondata = common.ignition(name=name, keys=keys, cmds=cmds, nets=nets, gateway=gateway, dns=dns,
                                               domain=domain, files=files, enableroot=enableroot, overrides=overrides,
                                               version=version, plan=plan, compact=False, removetls=True, image=image,
                                               vmuser=vmuser)
                ignitiondata = ignitiondata.replace('\n', '')
                initialization = types.Initialization(custom_script=ignitiondata)
        else:
            _template = types.Template(name='Blank')
        ostype = 'rhcos_x64' if ignition else None
        _os = types.OperatingSystem(boot=types.Boot(devices=[types.BootDevice.HD, types.BootDevice.CDROM]), type=ostype)
        console = types.Console(enabled=True)
        description = [f"filter={self.filtertag}"] if self.filtertag is not None else []
        for entry in [field for field in metadata if field in METADATA_FIELDS]:
            description.append(f'{entry}={metadata[entry]}')
        description = ','.join(description)
        profiles_service = self.conn.system_service().vnic_profiles_service()
        if not self.netprofiles:
            for prof in profiles_service.list():
                networkinfo = self.conn.follow_link(prof.network)
                netdatacenter = self.conn.follow_link(networkinfo.data_center)
                if netdatacenter.name == self.datacenter:
                    self.netprofiles[prof.name] = prof.id
        cpu = types.Cpu(topology=types.CpuTopology(cores=numcpus, sockets=1))
        try:
            if placement:
                placement_policy = None
                vmhost = types.Host(name=choice(placement))
            else:
                placement_policy = None
                vmhost = None
            vm = self.vms_service.add(types.Vm(name=name, cluster=types.Cluster(name=self.cluster), memory=memory,
                                               cpu=cpu, description=description, template=_template, console=console,
                                               os=_os, placement_policy=placement_policy,
                                               custom_properties=custom_properties), clone=clone)
            vm_service = self.vms_service.vm_service(vm.id)
        except Exception as e:
            if self.debug:
                print(e)
            return {'result': 'failure', 'reason': e}
        if vnc:
            vnc_console = types.GraphicsConsole(protocol=types.GraphicsType.VNC)
            graphics_console_service = vm_service.graphics_consoles_service()
            graphical_consoles = graphics_console_service
            for gc in graphical_consoles.list():
                graphics_console_service.console_service(gc.id).remove()
            graphics_console_service.add(vnc_console)
        cdroms_service = vm_service.cdroms_service()
        cdrom = cdroms_service.list()[0]
        cdrom_service = cdroms_service.cdrom_service(cdrom.id)
        if iso is not None:
            iso_id = iso
            disks_service = self.conn.system_service().disks_service()
            disksearch = disks_service.list(search=f'name={iso}')
            if disksearch:
                iso_id = disksearch[0].id
            try:
                cdrom_service.update(cdrom=types.Cdrom(file=types.File(id=iso_id)))
            except:
                return {'result': 'failure', 'reason': f"Iso {iso} not found"}
        timeout = 0
        while True:
            vm = vm_service.get()
            if vm.status == types.VmStatus.DOWN:
                break
            else:
                timeout += 5
                sleep(5)
                pprint(f"Waiting for vm {name} to be ready")
            if timeout > 80:
                return {'result': 'failure', 'reason': 'timeout waiting for vm to be ready'}
        if 'default' not in self.netprofiles:
            if 'ovirtmgmt' in self.netprofiles:
                self.netprofiles['default'] = self.netprofiles['ovirtmgmt']
            elif 'rhevm' in self.netprofiles:
                self.netprofiles['default'] = self.netprofiles['rhevm']
        nics_service = self.vms_service.vm_service(vm.id).nics_service()
        currentnics = len(nics_service.list())
        nic_configurations = []
        for index, net in enumerate(nets):
            netname = None
            netmask = None
            mac = None
            if isinstance(net, str):
                netname = net
            elif isinstance(net, dict) and 'name' in net:
                netname = net['name']
                ip = None
                mac = net.get('mac')
                netmask = next((e for e in [net.get('mask'), net.get('netmask')] if e is not None), None)
                gateway = net.get('gateway')
                noconf = net.get('noconf', False)
                if not noconf and 'ip' in net:
                    ip = net['ip']
                # if 'alias' in net:
                #    alias = net['alias']
                if not noconf and ips and len(ips) > index and ips[index] is not None:
                    ip = ips[index]
                if not noconf and ip is not None and netmask is not None and gateway is not None:
                    nic_configuration = types.NicConfiguration(name=f'eth{index}', on_boot=True,
                                                               boot_protocol=types.BootProtocol.STATIC,
                                                               ip=types.Ip(version=types.IpVersion.V4, address=ip,
                                                                           netmask=netmask, gateway=gateway))
                    nic_configurations.append(nic_configuration)
            if netname is not None and netname in self.netprofiles:
                profile_id = self.netprofiles[netname]
                mac = types.Mac(address=mac)
                nic = types.Nic(name=f'eth{index}', mac=mac, vnic_profile=types.VnicProfile(id=profile_id))
                if index < currentnics:
                    currentnic = nics_service.list()[index]
                    currentnic_service = nics_service.nic_service(currentnic.id)
                    currentnic_service.update(nic=nic)
                else:
                    nics_service.add(nic)
        # disk_attachments_service = self.vms_service.vm_service(vm.id).disk_attachments_service()
        for index, disk in enumerate(disks):
            diskpool = pool
            diskthin = True
            disksize = 10
            if isinstance(disk, int):
                disksize = disk
            elif isinstance(disk, str) and disk.isdigit():
                disksize = int(disk)
            elif isinstance(disk, dict):
                disksize = disk.get('size', disksize)
                diskpool = disk.get('pool', pool)
                diskthin = disk.get('thin', diskthin)
            if index == 0 and image is not None:
                if disksize != 10:
                    self.update_image_size(vm.id, disksize)
                continue
            newdisk = self.add_disk(name, disksize, pool=diskpool, thin=diskthin)
            if newdisk['result'] == 'failure':
                return {'result': 'failure', 'reason': newdisk['reason']}
        if cloudinit and not custom_properties and initialization is None:
            custom_script = ''
            if storemetadata and overrides:
                storeoverrides = {k: overrides[k] for k in overrides if k not in ['password', 'rhnpassword',
                                                                                  'rhnactivationkey']}
                storedata = {'path': '/root/.metadata',
                             'content': yaml.dump(storeoverrides, default_flow_style=False, indent=2)}
                if files:
                    files.append(storedata)
                else:
                    files = [storedata]
            if files:
                data = common.process_files(files=files, overrides=overrides)
                if data != '':
                    custom_script += "write_files:\n"
                    custom_script += data
            cmds.insert(0, 'sed -i /192.168.122.1/d /etc/resolv.conf')
            cmds.insert(1, 'sleep 10')
            ignorednics = "docker0 tun0 tun1 cni0 flannel.1"
            gcmds = []
            if image is not None and image.lower().startswith('debian'):
                gcmds.append('echo "deb http://download.opensuse.org/repositories/home:/evilissimo:/deb/Debian_7.0/ ./"'
                             ' >> /etc/apt/sources.list')
                gcmds.append('gpg -v -a --keyserver http://download.opensuse.org/repositories/home:/evilissimo:/deb/'
                             'Debian_7.0/Release.key --recv-keys D5C7F7C373A1A299')
                gcmds.append('gpg --export --armor 73A1A299 | apt-key add -')
                gcmds.append('apt-get update')
                gcmds.append('apt-get -Y install ovirt-guest-agent')
                gcmds.append(f'sed -i "s/# ignored_nics.*/ignored_nics = {ignorednics}/" /etc/ovirt-guest-agent.conf')
                gcmds.append('service ovirt-guest-agent enable')
                gcmds.append('service ovirt-guest-agent restart')
            if image is not None and [x for x in UBUNTUS if x in image.lower()]:
                gcmds.append('echo deb http://download.opensuse.org/repositories/home:/evilissimo:/ubuntu:/16.04/'
                             'xUbuntu_16.04/ /')
                gcmds.append('wget http://download.opensuse.org/repositories/home:/evilissimo:/ubuntu:/16.04/'
                             'xUbuntu_16.04//Release.key')
                gcmds.append('apt-key add - < Release.key')
                gcmds.append('apt-get update')
                gcmds.append('apt-get -Y install ovirt-guest-agent')
                gcmds.append('sed -i "s/# ignored_nics.*/ignored_nics = docker0,tun0/" /etc/ovirt-guest-agent.conf')
            if gcmds:
                index = 1
                if image is not None and image.startswith('rhel'):
                    subindex = [i for i, value in enumerate(cmds) if value.startswith('subscription-manager')]
                    if subindex:
                        index = subindex.pop() + 1
                cmds = cmds[:index] + gcmds + cmds[index:]
            data = common.process_cmds(cmds=cmds, overrides=overrides)
            custom_script += "runcmd:\n"
            custom_script += data
            custom_script = None if custom_script == '' else custom_script
            vmuser = vmuser or 'root'
            user_name = common.get_user(image) if image is not None else vmuser
            root_password = None
            # dns_servers = '8.8.8.8 1.1.1.1'
            # dns_servers = dns if dns is not None else '8.8.8.8 1.1.1.1'
            publickeyfile = get_ssh_pub_key()
            if publickeyfile is not None:
                publickeyfile = open(publickeyfile).read().rstrip()
                if not keys:
                    keys = [publickeyfile]
                else:
                    keys.append(publickeyfile)
            keys = '\n'.join(keys)
            host_name = f"{name}.{domain}" if domain is not None else name
            initialization = types.Initialization(user_name=user_name, root_password=root_password,
                                                  authorized_ssh_keys=keys, host_name=host_name,
                                                  nic_configurations=nic_configurations, dns_servers=dns,
                                                  dns_search=domain, custom_script=custom_script)
        if start:
            vm_data = {'use_cloud_init': cloudinit, 'vm': types.Vm(initialization=initialization, host=vmhost)}
            if ignition:
                vm_data['use_ignition'] = True
            vm_service.start(**vm_data)
        if ip is not None:
            self.update_metadata(name, 'ip', ip)
        return {'result': 'success'}

    def start(self, name):
        vmsearch = self.vms_service.list(search=f'name={name}')
        if not vmsearch:
            error(f"VM {name} not found")
            return {'result': 'failure', 'reason': f"VM {name} not found"}
        vminfo = vmsearch[0]
        if str(vminfo.status) == 'down':
            vm = self.vms_service.vm_service(vmsearch[0].id)
            vm.start()
        return {'result': 'success'}

    def stop(self, name, soft=False):
        vmsearch = self.vms_service.list(search=f'name={name}')
        if not vmsearch:
            error(f"VM {name} not found")
            return {'result': 'failure', 'reason': f"VM {name} not found"}
        vminfo = vmsearch[0]
        if str(vminfo.status) != 'down':
            vm = self.vms_service.vm_service(vmsearch[0].id)
            vm.stop()
        return {'result': 'success'}

    def create_snapshot(self, name, base):
        vmsearch = self.vms_service.list(search=f'name={base}')
        if not vmsearch:
            error(f"VM {base} not found")
            return {'result': 'failure', 'reason': f"VM {base} not found"}
        vm = vmsearch[0]
        snapshots_service = self.vms_service.vm_service(vm.id).snapshots_service()
        snapshots_service.add(types.Snapshot(description=name))
        return

    def delete_snapshot(self, name, base):
        print("not implemented")
        return

    def list_snapshots(self, base):
        print("not implemented")
        return []

    def revert_snapshot(self, name, base):
        print("not implemented")
        return

    def restart(self, name):
        vmsearch = self.vms_service.list(search=f'name={name}')
        if not vmsearch:
            error(f"VM {name} not found")
            return {'result': 'failure', 'reason': f"VM {name} not found"}
        vm = vmsearch[0]
        status = str(vm.status)
        vm = self.vms_service.vm_service(vmsearch[0].id)
        if status == 'down':
            vm.start()
        else:
            vm.reboot()
        return {'result': 'success'}

    def info_host(self):
        data = {}
        api = self.conn.system_service().get()
        system_service = self.conn.system_service()
        data['version'] = api.product_info.version.full_version
        if api.summary.vms is not None:
            data['vms_running'] = api.summary.vms.total
        if api.summary.hosts is not None:
            data['hosts_summary'] = api.summary.hosts.total
        hosts = []
        hosts_service = self.conn.system_service().hosts_service()
        for host in hosts_service.list():
            hosts.append(host.name)
        data['hosts'] = hosts
        storage = []
        if api.summary.storage_domains is not None:
            data['storage_summary'] = api.summary.storage_domains.total
        sds_service = system_service.storage_domains_service()
        for sd in sds_service.list():
            storage.append(f"Storage Domain: {sd.name}")
        data['storage'] = storage
        return data

    def status(self, name):
        print("not implemented")
        return

    def list(self):
        vms = []
        system_service = self.conn.system_service()
        if self.filtertag is not None:
            vmslist = self.vms_service.list(search=f'description=plan*,filter={self.filtertag}*')
        elif self.filteruser:
            users_service = system_service.users_service()
            user_name = f'{self.user}-authz' if '@internal' in self.user else self.user
            userid = [u.id for u in users_service.list() if u.user_name == user_name][0]
            vmslist = self.vms_service.list(search=f'created_by_user_id={userid}')
        elif self.filtervms:
            vmslist = self.vms_service.list(search='description=plan=*,profile=*')
        else:
            vmslist = self.vms_service.list()
        for vm in vmslist:
            try:
                vms.append(self.info(vm.name, vm=vm))
            except:
                continue
        return sorted(vms, key=lambda x: x['name'])

    def console(self, name, tunnel=False, tunnelhost=None, tunnelport=22, tunneluser='root', web=False):
        connectiondetails = None
        vmsearch = self.vms_service.list(search=f'name={name}')
        if not vmsearch:
            error(f"VM {name} not found")
            return {'result': 'failure', 'reason': f"VM {name} not found"}
        vm = vmsearch[0]
        vm_service = self.vms_service.vm_service(vm.id)
        consoles_service = vm_service.graphics_consoles_service()
        consoles = consoles_service.list(current=True)
        for c in consoles:
            console_service = consoles_service.console_service(c.id)
            ticket = console_service.ticket()
            if str(c.protocol) == 'spice':
                ocacontent = open(self.ca_file).read().replace('\n', '\\n')
                try:
                    host = self.conn.follow_link(vm.host)
                    hostname = host.address
                except:
                    hostname = c.address
                subject = f'O={self.org},CN={hostname}'
                if tunnel:
                    localport1 = common.get_free_port()
                    localport2 = common.get_free_port()
                    command = "ssh -o LogLevel=QUIET -f -p %s -L %s:%s:%s -L %s:%s:%s %s@%s sleep 5"\
                        % (self.port, localport1, c.address, c.port, localport2, c.address, c.tls_port, self.ssh_user,
                           self.host)
                    os.system(command)
                address = '127.0.0.1' if tunnel else c.address
                port = localport1 if tunnel else c.port
                sport = localport2 if tunnel else c.tls_port
                connectiondetails = """[virt-viewer]
type=spice
host={address}
port={port}
password={ticket}
tls-port={sport}
fullscreen=0
title={name}:%d
enable-smartcard=0
enable-usb-autoshare=1
delete-this-file=1
usb-filter=-1,-1,-1,-1,0
tls-ciphers=DEFAULT
host-subject={subject}
ca={ocacontent}
toggle-fullscreen=shift+f11
release-cursor=shift+f12
secure-attention=ctrl+alt+end
secure-channels=main;inputs;cursor;playback;record;display;usbredir;smartcard""".format(subject=subject,
                                                                                        ocacontent=ocacontent,
                                                                                        address=address,
                                                                                        port=port,
                                                                                        sport=sport,
                                                                                        ticket=ticket.value,
                                                                                        name=name)
            elif str(c.protocol) == 'vnc':
                if tunnel:
                    localport1 = common.get_free_port()
                    command = "ssh -o LogLevel=QUIET -f -p %s -L %s:%s:%s %s@%s sleep 5"\
                        % (self.port, localport1, c.address, c.port, self.ssh_user, self.host)
                    os.system(command)
                address = '127.0.0.1' if tunnel else c.address
                port = localport1 if tunnel else c.port
                connectiondetails = """[virt-viewer]
type=vnc
host={address}
port={port}
password={ticket}
title={name}:%d
delete-this-file=1
toggle-fullscreen=shift+f11
release-cursor=shift+f12""".format(address=address, port=port, ticket=ticket.value, name=name)
        if connectiondetails is None:
            error(f"Couldn't retrieve connection details for {name}")
            sys.exit(1)
        if web:
            return "%s://%s:%s+%s" % (c.protocol, address, sport if str(c.protocol) == 'spice' else port, ticket.value)
        with open("/tmp/console.vv", "w") as f:
            f.write(connectiondetails)
        if self.debug or os.path.exists("/i_am_a_container"):
            msg = f"Use remote-viewer with this:\n{connectiondetails}" if not self.debug else connectiondetails
            pprint(msg)
        elif os.path.exists('/Users') and str(c.protocol) == 'vnc':
            pprint(f"Use password {ticket.value}")
            os.popen(f"open vnc://{address}:{port}")
        else:
            os.popen("remote-viewer /tmp/console.vv &")
        return

    def serialconsole(self, name, web=False):
        system_service = self.conn.system_service()
        users_service = system_service.users_service()
        user = users_service.list(search=f'usrname={self.user}-authz')[0]
        user_service = users_service.user_service(user.id)
        vmsearch = self.vms_service.list(search=f'name={name}')
        if not vmsearch:
            error(f"VM {name} not found")
            return {'result': 'failure', 'reason': f"VM {name} not found"}
        vm = vmsearch[0]
        permissions_service = self.vms_service.vm_service(vm.id).permissions_service()
        permissions_service.add(types.Permission(user=types.User(id=user.id), role=types.Role(name='UserVmManager')))
        keys_service = user_service.ssh_public_keys_service()
        publickeyfile = get_ssh_pub_key()
        if publickeyfile is None:
            error("neither id_rsa, id_dsa nor id_ed25519 public keys found in your .ssh directory. This is required")
            return
        publickeyfile = open(publickeyfile).read().rstrip()
        try:
            keys_service.add(key=types.SshPublicKey(content=publickeyfile))
        except:
            pass
        command = f"ssh -t -p 2222 ovirt-vmconsole@{self.host} connect --vm-name {name}"
        if web:
            return command
        call(command, shell=True)
        return

    def dnsinfo(self, name):
        vmsearch = self.vms_service.list(search=f'name={name}')
        if not vmsearch:
            return None, None
        vm = vmsearch[0]
        dnsclient, domain = None, None
        description = vm.description.split(',')
        for description in vm.description.split(','):
            desc = description.split('=')
            if len(desc) == 2:
                if desc[0] == 'dnsclient':
                    dnsclient = desc[1]
                if desc[0] == 'domain':
                    domain = desc[1]
        return dnsclient, domain

    def info(self, name, vm=None, debug=False):
        conn = self.conn
        minimal = False
        if vm is None:
            vmsearch = self.vms_service.list(search=f'name={name}')
            if not vmsearch:
                error(f"VM {name} not found")
                return {}
            vm = vmsearch[0]
        else:
            minimal = True
        status = str(vm.status)
        yamlinfo = {'name': vm.name, 'disks': [], 'nets': [], 'status': status, 'instanceid': vm.id}
        template = conn.follow_link(vm.template)
        image = template.name
        yamlinfo['image'] = image
        yamlinfo['user'] = common.get_user(image)
        for description in vm.description.split(','):
            desc = description.split('=')
            if len(desc) == 2:
                if desc[0] == 'filter':
                    continue
                else:
                    yamlinfo[desc[0]] = desc[1]
        try:
            if status == 'up':
                host = conn.follow_link(vm.host)
                yamlinfo['host'] = host.name
        except:
            pass
        yamlinfo['memory'] = int(vm._memory / 1024 / 1024)
        cpus = vm.cpu.topology.cores * vm.cpu.topology.sockets
        yamlinfo['numcpus'] = cpus
        yamlinfo['creationdate'] = vm._creation_time.strftime("%d-%m-%Y %H:%M")
        devices = self.vms_service.vm_service(vm.id).reported_devices_service().list()
        ips = []
        for device in devices:
            if device.ips:
                for ip in device.ips:
                    if str(ip.version) == 'v4' and ip.address not in ['172.17.0.1', '127.0.0.1']:
                        ips.append(ip.address)
        nics = self.vms_service.vm_service(vm.id).nics_service().list()
        profiles_service = self.conn.system_service().vnic_profiles_service()
        if ips:
            yamlinfo['ip'] = ips[0]
            if len(ips) > 1:
                yamlinfo['ips'] = ips
        if minimal:
            return yamlinfo
        if not self.netprofiles:
            self.netprofiles = {}
            for profile in profiles_service.list():
                self.netprofiles[profile.id] = profile.name
        for nic in nics:
            device = nic.name
            mac = nic.mac.address
            network = 'N/A'
            if nic.vnic_profile is not None and nic.vnic_profile.id in self.netprofiles:
                network = self.netprofiles[nic.vnic_profile.id]
            network_type = str(nic.interface)
            yamlinfo['nets'].append({'device': device, 'mac': mac, 'net': network, 'type': network_type})
        attachments = self.vms_service.vm_service(vm.id).disk_attachments_service().list()
        for attachment in attachments:
            disk = conn.follow_link(attachment.disk)
            storagedomain = conn.follow_link(disk.storage_domains[0]).name if disk.storage_domains else ''
            device = disk.name
            disksize = int(disk.provisioned_size / 2**30)
            diskformat = str(disk.format)
            drivertype = str(disk.content_type)
            path = disk.id
            yamlinfo['disks'].append({'device': device, 'size': disksize, 'format': diskformat, 'type': drivertype,
                                      'path': f"{storagedomain}/{path}"})
        if image is None and 'kubetype' in yamlinfo and yamlinfo['kubetype'] == 'openshift':
            yamlinfo['user'] = 'core'
        cdroms_service = self.vms_service.vm_service(vm.id).cdroms_service()
        cdroms = cdroms_service.list()
        if cdroms:
            cdrom = cdroms[0]
            if cdrom.file is not None:
                iso = cdrom.file.id
                disks_service = self.conn.system_service().disks_service()
                disksearch = disks_service.list(search=f'id={iso}')
                if disksearch:
                    yamlinfo['iso'] = disksearch[0].name
        if debug:
            yamlinfo['debug'] = vars(vm)
        return yamlinfo

    def ip(self, name):
        vmsearch = self.vms_service.list(search=f'name={name}')
        if not vmsearch:
            error(f"VM {name} not found")
            return None
        vm = vmsearch[0]
        ips = []
        devices = self.vms_service.vm_service(vm.id).reported_devices_service().list()
        for device in devices:
            if device.ips:
                for i in device.ips:
                    if str(i.version) == 'v4' and i.address not in ['172.17.0.1', '127.0.0.1']:
                        ips.append(i.address)
        if not ips:
            return None
        else:
            return ips[-1]

    def volumes(self, iso=False, extended=False):
        if iso:
            isos = []
            for pool in self.conn.system_service().storage_domains_service().list():
                sd_service = self.sds_service.storage_domain_service(pool.id)
                if str(pool.type) == 'iso':
                    file_service = sd_service.files_service()
                    for isofile in file_service.list():
                        isos.append(isofile._name)
                else:
                    disks_service = sd_service.disks_service()
                    for disk in disks_service.list():
                        if disk.name.endswith('.iso'):
                            isos.append(disk.name)
            return isos
        else:
            images = []
            templates_service = self.templates_service
            templateslist = templates_service.list()
            for template in templateslist:
                if template.name != 'Blank':
                    images.append(template.name)
            return images

    def delete(self, name, snapshots=False):
        vmsearch = self.vms_service.list(search=f'name={name}')
        if not vmsearch:
            error(f"VM {name} not found")
            return {'result': 'failure', 'reason': f"VM {name} not found"}
        vminfo = vmsearch[0]
        vm = self.vms_service.vm_service(vminfo.id)
        if str(vminfo.status) not in ['down', 'unknown']:
            vm.stop()
            while True:
                sleep(5)
                currentvm = vm.get()
                if currentvm.status == types.VmStatus.DOWN:
                    break
        vm.remove()
        return {'result': 'success'}

    def clone(self, old, new, full=False, start=False):
        print("not implemented")
        return

    def update_metadata(self, name, metatype, metavalue, append=False):
        vmsearch = self.vms_service.list(search=f'name={name}')
        if not vmsearch:
            error(f"VM {name} not found")
            return
        vminfo = vmsearch[0]
        found = False
        newdescription = []
        olddescription = vminfo.description.split(',')
        for index, description in enumerate(olddescription):
            desc = description.split('=')
            if len(desc) == 2 and desc[0] == metatype:
                found = True
                if append:
                    oldvalue = desc[1]
                    metavalue = f"{oldvalue}+{metavalue}"
                newdescription.append(f"{metatype}={metavalue}")
            else:
                newdescription.append(description)
        if not found:
            newdescription.append(f"{metatype}={metavalue}")
        description = ','.join(newdescription)
        vm = self.vms_service.vm_service(vmsearch[0].id)
        vm.update(vm=types.Vm(description=description))
        return

    def update_memory(self, name, memory):
        vmsearch = self.vms_service.list(search=f'name={name}')
        if not vmsearch:
            error(f"VM {name} not found")
            return {'result': 'failure', 'reason': f"VM {name} not found"}
        vminfo = vmsearch[0]
        vm = self.vms_service.vm_service(vminfo.id)
        if str(vminfo.status) == 'up':
            warning("Note it will only be effective upon next start")
        memory = int(memory) * 1024 * 1024
        vm.update(vm=types.Vm(memory=memory))
        return {'result': 'success'}

    def update_cpus(self, name, numcpus):
        vmsearch = self.vms_service.list(search=f'name={name}')
        if not vmsearch:
            error(f"VM {name} not found")
            return {'result': 'failure', 'reason': f"VM {name} not found"}
        vminfo = vmsearch[0]
        if str(vminfo.status) == 'up':
            warning("Note it will only be effective upon next start")
        vm = self.vms_service.vm_service(vminfo.id)
        cpu = types.Cpu(topology=types.CpuTopology(cores=numcpus, sockets=1))
        vm.update(vm=types.Vm(cpu=cpu))
        return {'result': 'success'}

    def update_start(self, name, start=True):
        print("not implemented")
        return

    def update_information(self, name, information):
        self.update_metadata(name, 'information', information)
        return

    def update_iso(self, name, iso):
        vmsearch = self.vms_service.list(search=f'name={name}')
        if not vmsearch:
            error(f"VM {name} not found")
            return {'result': 'failure', 'reason': f"VM {name} not found"}
        vminfo = vmsearch[0]
        vm = self.vms_service.vm_service(vminfo.id)
        cdroms_service = vm.cdroms_service()
        cdrom = cdroms_service.list()[0]
        cdrom_service = cdroms_service.cdrom_service(cdrom.id)
        current = False if str(vminfo.status) == 'down' else True
        if iso is not None and iso != '':
            disks_service = self.conn.system_service().disks_service()
            disksearch = disks_service.list(search=f'name={iso}')
            if disksearch:
                iso_id = disksearch[0].id
            else:
                error(f"Iso {iso} not found")
                return {'result': 'failure', 'reason': f"Iso {iso} not found"}
            try:
                cdrom_service.update(cdrom=types.Cdrom(file=types.File(id=iso_id)), current=current)
            except Exception as e:
                error(f"Hit issue {e.fault._detail}")
                return {'result': 'failure', 'reason': f"Hit issue {e.fault._detail}"}
        else:
            cdrom_service.update(cdrom=types.Cdrom(file=types.File(id='')), current=current)
        return {'result': 'success'}

    def update_flavor(self, name, flavor):
        print("Not implemented")
        return {'result': 'success'}

    def create_disk(self, name, size, pool=None, thin=True, image=None):
        print("not implemented")
        return

    def add_disk(self, name, size, pool=None, thin=True, image=None,
                 shareable=False, existing=None, interface='virtio', novm=False, overrides={}, diskname=None):
        size *= 2**30
        system_service = self.conn.system_service()
        sds_service = system_service.storage_domains_service()
        poolcheck = sds_service.list(search=f'name={pool}')
        if not poolcheck:
            error(f"Pool {pool} not found")
            return {'result': 'failure', 'reason': f"Pool {pool} not found"}
        vmsearch = self.vms_service.list(search=f'name={name}')
        if not vmsearch:
            error(f"VM {name} not found")
            return {'result': 'failure', 'reason': f"VM {name} not found"}
        vm = self.vms_service.vm_service(vmsearch[0].id)
        disk_attachments_service = vm.disk_attachments_service()
        currentdisk = len(disk_attachments_service.list())
        diskindex = currentdisk + 1 if currentdisk > 0 else 0
        diskname = f'{name}_Disk{diskindex}'
        _format = types.DiskFormat.COW if thin else types.DiskFormat.RAW
        storagedomain = types.StorageDomain(name=pool)
        bootable = True if diskindex == 0 else False
        disk_attachment = types.DiskAttachment(disk=types.Disk(name=diskname, format=_format, provisioned_size=size,
                                                               storage_domains=[storagedomain]),
                                               interface=types.DiskInterface.VIRTIO, bootable=bootable, active=True)
        disk_attachment = disk_attachments_service.add(disk_attachment)
        disks_service = self.conn.system_service().disks_service()
        disk_service = disks_service.disk_service(disk_attachment.disk.id)
        timeout = 0
        while True:
            disk = disk_service.get()
            if disk.status == types.DiskStatus.OK:
                break
            else:
                timeout += 5
                sleep(5)
                pprint(f"Waiting for disk {diskname} to be ready")
            if timeout > 40:
                return {'result': 'failure', 'reason': f'timeout waiting for disk {diskname} to be ready'}
        return {'result': 'success'}

    def update_image_size(self, vmid, size):
        size *= 2**30
        vm = self.vms_service.vm_service(vmid)
        disk_attachments_service = vm.disk_attachments_service()
        diskid = disk_attachments_service.list()[0].id
        disk_attachment_service = disk_attachments_service.attachment_service(diskid)
        disk_attachment = types.DiskAttachment(disk=types.Disk(provisioned_size=size))
        disk_attachment_service.update(disk_attachment)
        disks_service = self.conn.system_service().disks_service()
        disk_service = disks_service.disk_service(diskid)
        timeout = 0
        while True:
            disk = disk_service.get()
            diskname = disk.name
            if disk.status == types.DiskStatus.OK:
                break
            else:
                timeout += 5
                sleep(5)
                pprint(f"Waiting for image disk {diskname} to be resized")
            if timeout > 40:
                return {'result': 'failure', 'reason': f'timeout waiting for image disk {diskname} to be resized'}
        return {'result': 'success'}

    def delete_disk(self, name=None, diskname=None, pool=None, novm=False):
        vmsearch = self.vms_service.list(search=f'name={name}')
        if not vmsearch:
            error(f"VM {name} not found")
            return {'result': 'failure', 'reason': f"VM {name} not found"}
        vm = self.vms_service.vm_service(vmsearch[0].id)
        disk_attachments_service = vm.disk_attachments_service()
        for disk in disk_attachments_service.list():
            if disk.id == diskname:
                disk_attachment_service = disk_attachments_service.attachment_service(disk.id)
                disk_attachment_service.update(types.DiskAttachment(active=False))
                while True:
                    sleep(5)
                    disk_attachment = disk_attachment_service.get()
                    if not disk_attachment.active:
                        break
                disks_service = self.conn.system_service().disks_service()
                disk_service = disks_service.disk_service(disk.disk.id)
                disk_service.remove()
                return {'result': 'success'}
        error(f"Disk {diskname} not found")
        return {'result': 'failure', 'reason': f"Disk {diskname} not found"}

    def list_disks(self):
        volumes = {}
        disks_service = self.conn.system_service().disks_service()
        for disk in disks_service.list():
            diskname = disk._id
            if disk._storage_domains:
                pool = self.conn.follow_link(disk._storage_domains[0])._name
                path = disk._alias
                volumes[diskname] = {'pool': pool, 'path': path}
        return volumes

    def detach_disks(self, name):
        pprint(f"Detaching non primary disks from {name}")
        vmsearch = self.vms_service.list(search=f'name={name}')
        if not vmsearch:
            error(f"VM {name} not found")
            return {'result': 'failure', 'reason': f"VM {name} not found"}
        vm = self.vms_service.vm_service(vmsearch[0].id)
        disk_attachments_service = vm.disk_attachments_service()
        all_disks = disk_attachments_service.list()
        vm_disks = all_disks[1:] if len(all_disks) > 1 else []
        for disk in vm_disks:
            disk_attachment_service = disk_attachments_service.attachment_service(disk.id)
            disk_attachment_service.update(types.DiskAttachment(active=False))
            while True:
                sleep(5)
                disk_attachment = disk_attachment_service.get()
                if not disk_attachment.active:
                    break
        return {'result': 'success'}

    def add_nic(self, name, network, model='virtio'):
        vmsearch = self.vms_service.list(search=f'name={name}')
        if not vmsearch:
            error(f"VM {name} not found")
            return {'result': 'failure', 'reason': f"VM {name} not found"}
        vm = vmsearch[0]
        nics_service = self.vms_service.vm_service(vm.id).nics_service()
        index = len(nics_service.list())
        profiles_service = self.conn.system_service().vnic_profiles_service()
        netprofiles = {}
        for prof in profiles_service.list():
            networkinfo = self.conn.follow_link(prof.network)
            netdatacenter = self.conn.follow_link(networkinfo.data_center)
            if netdatacenter.name == self.datacenter:
                netprofiles[prof.name] = prof.id
        if 'default' not in netprofiles:
            if 'ovirtmgmt' in netprofiles:
                netprofiles['default'] = netprofiles['ovirtmgmt']
            elif 'rhevm' in netprofiles:
                netprofiles['default'] = netprofiles['rhevm']
        if network in netprofiles:
            profile_id = netprofiles[network]
            nics_service.add(types.Nic(name=f'eth{index}', vnic_profile=types.VnicProfile(id=profile_id)))
        else:
            return {'result': 'failure', 'reason': f"Network {network} not found"}
        return {'result': 'success'}

    def delete_nic(self, name, interface):
        vmsearch = self.vms_service.list(search=f'name={name}')
        if not vmsearch:
            error(f"VM {name} not found")
            return {'result': 'failure', 'reason': f"VM {name} not found"}
        vmid = vmsearch[0].id
        for nic in self.vms_service.vm_service(vmid).nics_service().list():
            if nic.name == interface:
                nics_service = self.vms_service.vm_service(vmid).nics_service()
                nic_service = nics_service.nic_service(nic.id)
                nic_service.remove()
                return {'result': 'success'}
        error(f"VM {name} not found")
        return {'result': 'failure', 'reason': f"VM {name} not found"}

    def create_pool(self, name, poolpath, pooltype='dir', user='qemu', thinpool=None):
        print("not implemented")
        return

    def delete_image(self, image, pool=None):
        if image.endswith('.iso'):
            pprint(f"Deleting Iso {image}")
            disks_service = self.conn.system_service().disks_service()
            disksearch = disks_service.list(search=f'name={image}')
            if not disksearch:
                return {'result': 'failure', 'reason': f"Image {image} not found"}
            else:
                disk_id = disksearch[0].id
                disk_service = disks_service.disk_service(disk_id)
                disk_service.remove()
                return {'result': 'success'}
        pprint("Deleting Template {image}")
        templates_service = self.templates_service
        templateslist = templates_service.list()
        for template in templateslist:
            if template.name == image:
                template_service = templates_service.template_service(template.id)
                template_service.remove()
                return {'result': 'success'}
        return {'result': 'failure', 'reason': f"Image {image} not found"}

    def add_image(self, url, pool, short=None, cmds=[], name=None, size=None, convert=False):
        downloaded = False
        shortimage = os.path.basename(url).split('?')[0]
        iso = True if shortimage.endswith('.iso') or name.endswith('.iso') else False
        if shortimage in self.volumes(iso=iso):
            pprint(f"Template {shortimage} already there")
            return {'result': 'success'}
        system_service = self.conn.system_service()
        profiles_service = self.conn.system_service().vnic_profiles_service()
        profile_id = None
        if not self.netprofiles:
            for prof in profiles_service.list():
                networkinfo = self.conn.follow_link(prof.network)
                netdatacenter = self.conn.follow_link(networkinfo.data_center)
                if netdatacenter.name == self.datacenter:
                    self.netprofiles[prof.name] = prof.id
        if 'default' not in self.netprofiles:
            if 'ovirtmgmt' in self.netprofiles:
                profile_id = self.netprofiles['ovirtmgmt']
            elif 'rhevm' in self.netprofiles:
                profile_id = self.netprofiles['rhevm']
        if profile_id is None:
            return {'result': 'failure', 'reason': "Couldn't find ovirtmgmt nor rhevm network!!!"}
        sds_service = system_service.storage_domains_service()
        poolcheck = sds_service.list(search=f'name={pool}')
        if not poolcheck:
            return {'result': 'failure', 'reason': f"Pool {pool} not found"}
        disks_service = self.conn.system_service().disks_service()
        disksearch = disks_service.list(search=f'alias={shortimage}')
        if not disksearch:
            if os.path.exists(url):
                pprint(f"Using {url} as path")
            elif not os.path.exists(f'/tmp/{shortimage}'):
                downloaded = True
                pprint(f"Downloading locally {shortimage}")
                downloadcmd = f"curl -Lo /tmp/{shortimage} -f '{url}'"
                code = os.system(downloadcmd)
                if code != 0:
                    return {'result': 'failure', 'reason': "Unable to download indicated image"}
            else:
                pprint(f"Using found /tmp/{shortimage}")
            BUF_SIZE = 128 * 1024
            image_path = os.path.abspath(url) if os.path.exists(url) else f'/tmp/{shortimage}'
            extensions = {'bz2': 'bunzip2 -f', 'gz': 'gunzip -f', 'xz': 'unxz -f', 'zst': 'zstd --decompress'}
            for extension in extensions:
                flag = '--decompress' if extension == 'zstd' else '-f'
                if shortimage.endswith(extension):
                    executable = extensions[extension]
                    if which(executable) is None:
                        pprint(f"{executable} not found. Can't uncompress image")
                        sys.exit(1)
                    else:
                        uncompresscmd = f"{executable} {flag} {image_path}"
                        os.system(uncompresscmd)
                        shortimage = shortimage.replace(f".{extension}", '')
                        image_path = f'/tmp/{shortimage}'
                        break
            image_size = os.path.getsize(image_path)
            if not iso:
                out = check_output(["qemu-img", "info", "--output", "json", image_path])
                image_info = json.loads(out)
                virtual_size = image_info["virtual-size"]
                content_type = types.DiskContentType.DATA
                disk_format = types.DiskFormat.COW
            else:
                virtual_size = image_size
                content_type = types.DiskContentType.ISO
                disk_format = types.DiskFormat.RAW
            sparse = True if disk_format == types.DiskFormat.COW else False
            disks_service = self.conn.system_service().disks_service()
            disk = disks_service.add(disk=types.Disk(name=os.path.basename(shortimage), content_type=content_type,
                                                     description='Kcli Uploaded disk', format=disk_format,
                                                     initial_size=image_size,
                                                     provisioned_size=virtual_size,
                                                     sparse=sparse,
                                                     storage_domains=[types.StorageDomain(name=pool)]))
            disk_service = disks_service.disk_service(disk.id)
            disk_id = disk.id
            while True:
                sleep(5)
                disk = disk_service.get()
                if disk.status == types.DiskStatus.OK:
                    break
            transfers_service = self.conn.system_service().image_transfers_service()
            transfer = transfers_service.add(types.ImageTransfer(image=types.Image(id=disk_id)))
            transfer_service = transfers_service.image_transfer_service(transfer.id)
            while transfer.phase == types.ImageTransferPhase.INITIALIZING:
                sleep(1)
                transfer = transfer_service.get()
            destination_url = urlparse(transfer.proxy_url)
            context = ssl._create_unverified_context()
            proxy_connection = HTTPSConnection(destination_url.hostname, destination_url.port, context=context)
            proxy_connection.putrequest("PUT", destination_url.path)
            proxy_connection.putheader('Content-Length', image_size)
            proxy_connection.endheaders()
            last_progress = time()
            pprint(f"Uploading image {shortimage}")
            with open(image_path, "rb") as disk:
                pos = 0
                while pos < image_size:
                    to_read = min(image_size - pos, BUF_SIZE)
                    chunk = disk.read(to_read)
                    if not chunk:
                        transfer_service.pause()
                        raise RuntimeError(f"Unexpected end of file at pos={pos}")
                    proxy_connection.send(chunk)
                    pos += len(chunk)
                    now = time()
                    if now - last_progress > 10:
                        pprint("Uploaded %.2f%%" % (float(pos) / image_size * 100))
                        last_progress = now
            response = proxy_connection.getresponse()
            if response.status != 200:
                transfer_service.pause()
                return {'result': 'failure', 'reason': f"Upload failed: {response.status} {response.reason}"}
            transfer_service.finalize()
            proxy_connection.close()
        else:
            disk_id = disksearch[0].id
            disk_service = disks_service.disk_service(disk_id)
        if iso:
            return {'result': 'success'}
        _template = types.Template(name='Blank')
        _os = types.OperatingSystem(boot=types.Boot(devices=[types.BootDevice.HD, types.BootDevice.CDROM]))
        console = types.Console(enabled=True)
        cpu = types.Cpu(topology=types.CpuTopology(cores=2, sockets=1))
        memory = 1024 * 1024 * 1024
        tempname = 'kcli_import' + ''.join(choice(ascii_lowercase) for _ in range(5))
        tempvm = self.vms_service.add(types.Vm(name=tempname, cluster=types.Cluster(name=self.cluster),
                                               memory=memory, cpu=cpu, template=_template, console=console, os=_os),
                                      clone=False)
        while True:
            pprint(f"Preparing temporary vm {tempname}")
            sleep(5)
            disk_service = disks_service.disk_service(disk_id)
            disk = disk_service.get()
            if disk.status == types.DiskStatus.OK:
                break
        tempvm_service = self.vms_service.vm_service(tempvm.id)
        _format = types.DiskFormat.COW
        storagedomain = types.StorageDomain(name=pool)
        disk_attachments = types.DiskAttachment(disk=types.Disk(id=disk_id, format=_format,
                                                                storage_domains=[storagedomain]),
                                                interface=types.DiskInterface.VIRTIO,
                                                bootable=True, active=True)
        disk_attachments_service = tempvm_service.disk_attachments_service()
        disk_attachments_service.add(disk_attachments)
        nics_service = self.vms_service.vm_service(tempvm.id).nics_service()
        nics_service.add(types.Nic(name='eth0', vnic_profile=types.VnicProfile(id=profile_id)))
        template = self.templates_service.add_from_vm(template=types.Template(name=shortimage, vm=tempvm))
        template_service = self.templates_service.template_service(template.id)
        while True:
            pprint("Converting temporary vm to template")
            sleep(5)
            template = template_service.get()
            if template.status == types.TemplateStatus.OK:
                break
        tempvmsearch = self.vms_service.list(search=f'name={tempname}')
        tempvminfo = tempvmsearch[0]
        tempvm = self.vms_service.vm_service(tempvminfo.id)
        tempvm.remove()
        if downloaded:
            os.remove(f'/tmp/{shortimage}')
        return {'result': 'success'}

    def create_network(self, name, cidr=None, dhcp=True, nat=True, domain=None, plan='kvirt', overrides={}):
        if 'vlan' not in overrides:
            return {'result': 'failure', 'reason': "Missing Vlan"}
        vlan = overrides['vlan']
        networks_service = self.conn.system_service().networks_service()
        networks_service.add(network=types.Network(name=name, data_center=types.DataCenter(name=self.datacenter),
                                                   vlan=types.Vlan(vlan), usages=[types.NetworkUsage.VM], mtu=1500))
        return

    def delete_network(self, name=None, cidr=None, force=False):
        print("not implemented")
        return

    def list_pools(self):
        return [pool.name for pool in self.conn.system_service().storage_domains_service().list()]

    def list_networks(self):
        networks = {}
        networks_service = self.conn.system_service().networks_service()
        for network in networks_service.list():
            networkname = network._name
            cidr = 'N/A'
            vlan = network._vlan
            if vlan is not None:
                cidr = f"vlan {vlan.id}"
            dhcp = network._id
            domainname = network._data_center
            domainname = self.conn.follow_link(network._data_center).name
            mode = network._description
            networks[networkname] = {'cidr': cidr, 'dhcp': dhcp, 'domain': domainname, 'type': 'routed', 'mode': mode}
        return networks

    def info_network(self, name):
        networkinfo = common.info_network(self, name)
        return networkinfo

    def list_subnets(self):
        print("not implemented")
        return {}

    def delete_pool(self, name, full=False):
        print("not implemented")
        return

    def network_ports(self, name):
        print("not implemented")
        return

    def vm_ports(self, name):
        return []

# returns the path of the pool, if it makes sense. used by kcli list --pools
    def get_pool_path(self, pool):
        poolsearch = self.conn.system_service().storage_domains_service().list(search=f'name={pool}')
        if not poolsearch:
            error(f"Pool {pool} not found")
            return {'result': 'failure', 'reason': f"Pool {pool} not found"}
        pool = poolsearch[0]
        return pool.storage.path

    def list_flavors(self):
        return []

    def export(self, name, image=None):
        vmsearch = self.vms_service.list(search=f'name={name}')
        if not vmsearch:
            error(f"VM {name} not found")
            return {'result': 'failure', 'reason': f"VM {name} not found"}
        vminfo = vmsearch[0]
        vm = self.vms_service.vm_service(vminfo.id)
        if str(vminfo.status) == 'up':
            vm.stop()
        attachments = self.conn.follow_link(vminfo.disk_attachments)
        disk_ids = [attachment.disk.id for attachment in attachments]
        _format = types.DiskFormat.COW
        attachments = [types.DiskAttachment(disk=types.Disk(id=disk_id, format=_format)) for disk_id in disk_ids]
        newvm = types.Vm(id=vminfo.id, disk_attachments=attachments)
        newname = image if image is not None else name
        template = types.Template(name=newname, vm=newvm)
        template = self.templates_service.add(template=template)
        template_service = self.templates_service.template_service(template.id)
        while True:
            sleep(5)
            template = template_service.get()
            if template.status == types.TemplateStatus.OK:
                break
        return {'result': 'success'}

    def get_hostname(self, address):
        conn = self.conn
        try:
            hosts_service = conn.system_service().hosts_service()
            for host in hosts_service.list():
                print(vars(host))
                break
            # host = hosts_service.list(search='name=myhost')[0]
            # host_service = hosts_service.host_service(host.id)
        except:
            return address

    def list_dns(self, domain):
        return []

    def create_bucket(self, bucket, public=False):
        print("not implemented")
        return

    def delete_bucket(self, bucket):
        print("not implemented")
        return

    def delete_from_bucket(self, bucket, path):
        print("not implemented")
        return

    def download_from_bucket(self, bucket, path):
        print("not implemented")
        return

    def upload_to_bucket(self, bucket, path, overrides={}, temp_url=False, public=False):
        print("not implemented")
        return

    def list_buckets(self):
        print("not implemented")
        return []

    def list_bucketfiles(self, bucket):
        print("not implemented")
        return []

    def openshift_installer_data(self, pool):
        clusters = self.conn.system_service().clusters_service().list(search=f'name={self.cluster}')
        if clusters:
            clusterid = clusters[0].id
        pools = self.conn.system_service().storage_domains_service().list(search=f'name={pool}')
        if pools:
            poolid = pools[0].id
        profiles_service = self.conn.system_service().vnic_profiles_service()
        for prof in profiles_service.list():
            networkinfo = self.conn.follow_link(prof.network)
            netdatacenter = self.conn.follow_link(networkinfo.data_center)
            if netdatacenter.name == self.datacenter:
                vnicid = prof.id
                break
        return clusterid, poolid, vnicid

    def reserve_dns(self, name, nets=[], domain=None, ip=None, alias=[], force=False, primary=False):
        print("not implemented")
        return

    def update_nic(self, name, index, network):
        print("not implemented")

    def update_network(self, name, dhcp=None, nat=None, domain=None, plan=None, overrides={}):
        print("not implemented")
        return {'result': 'success'}

    def list_security_groups(self, network=None):
        print("not implemented")
        return []

    def create_security_group(self, name, overrides={}):
        print("not implemented")
        return {'result': 'success'}

    def delete_security_group(self, name):
        print("not implemented")
        return {'result': 'success'}

    def update_security_group(self, name, overrides={}):
        print("not implemented")
        return {'result': 'success'}

    def info_subnet(self, name):
        print("not implemented")
        return {}

    def create_subnet(self, name, cidr, dhcp=True, nat=True, domain=None, plan='kvirt', overrides={}):
        print("not implemented")
        return {'result': 'success'}

    def delete_subnet(self, name, force=False):
        print("not implemented")
        return {'result': 'success'}

    def update_subnet(self, name, overrides={}):
        print("not implemented")
        return {'result': 'success'}

    def list_dns_zones(self):
        print("not implemented")
        return []
