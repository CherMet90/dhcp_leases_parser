import inspect
import ipaddress
import os
import re
import traceback

import pynetbox
from colorama import init

from dhcp_leases_parser.errors import Error, NonCriticalError
from dhcp_leases_parser.log import logger

# Initialize Colorama
init()


class NetboxDevice:
    # Получение переменных окружения
    # =====================================================================
    @staticmethod
    def __get_env_variable(variable_key):
        variable_value = os.environ.get(variable_key)
        if variable_value is None:
            raise ValueError(f"Missing environment variable: {variable_key}")
        return variable_value

    __netbox_url = __get_env_variable("NETBOX_URL")
    __netbox_token = __get_env_variable("NETBOX_TOKEN")
    # ====================================================================

    # Создание netbox соединения

    @classmethod
    def create_connection(cls):
        try:
            cls.netbox_connection = pynetbox.api(
                url=cls.__netbox_url,
                token=cls.__netbox_token
            )
            logger.info("Connection to NetBox established")
        except Exception as e:
            traceback.print_exc()
            raise e
        cls.netbox_prefixes = list(cls.netbox_connection.ipam.prefixes.all())

    # Получение вланов сайта из netbox
    @classmethod
    def get_vlans(cls, site_slug):
        try:
            vlans = list(
                cls.netbox_connection.ipam.vlans.filter(site=site_slug))
            # Extract VLAN IDs from the objects
            vlan_ids = [str(vlan.vid) for vlan in vlans]
            logger.debug(f"Found {len(vlan_ids)} VLANs for site {site_slug}")
            return vlans
        except pynetbox.core.query.RequestError as e:
            error_message = f"Request failed for site {site_slug}"
            calling_function = inspect.stack()[1].function
            NonCriticalError(error_message, site_slug, calling_function)
            return None

    @classmethod
    def get_netbox_ip(cls, ip_with_prefix, create=True):
        logger.info(f'Getting IP object from NetBox...')
        netbox_ip = cls.netbox_connection.ipam.ip_addresses.get(
            address=ip_with_prefix,
        )
        if not netbox_ip:
            if create:
                logger.info(f'IP {ip_with_prefix} not found in NetBox. Creating...')
                netbox_ip = cls.netbox_connection.ipam.ip_addresses.create(
                    address=ip_with_prefix,
                    status='active',
                )
            else:
                logger.info(f'IP {ip_with_prefix} not found in NetBox')
                return None
        
        parent_prefix = list(cls.netbox_connection.ipam.prefixes.filter(contains=ip_with_prefix))
        site_slug = parent_prefix[0].site.slug
        return netbox_ip, site_slug
    
    @classmethod
    def set_description(cls, device_name, interface_name, neighbor_name, neighbor_interface):
        netbox_interface = cls.netbox_connection.dcim.interfaces.get(
            name=interface_name, device=device_name
        )
        netbox_interface.description = f'-={neighbor_name}  {neighbor_interface}=-'
        netbox_interface.save()

    @classmethod
    def get_prefix_for_ip(cls, ip_addr):
        for prefix in cls.netbox_prefixes:
            if ipaddress.ip_address(ip_addr) in ipaddress.ip_network(prefix):
                return prefix
        raise Error("IP address not found in NetBox prefixes", ip_addr)
    
    @classmethod
    def create_ip_address(cls, ip, ip_with_prefix, status='active', description=''):
        logger.debug(f'Checking if IP address {ip_with_prefix} exists...')
        existing_ips = cls.netbox_connection.ipam.ip_addresses.filter(address=ip)
        if existing_ips:
            logger.debug(
                f'IP address {ip_with_prefix} already exists in NetBox (skipping creation, update only)')
            for existing_ip in existing_ips:
                if existing_ip.description != description or existing_ip.status.value != status:
                    logger.info(f'Updating IP address {ip_with_prefix}...')
                    existing_ip.description = description
                    existing_ip.status = status
                    existing_ip.save()
            return
        logger.info(f'Creating IP address {ip_with_prefix}...')
        cls.netbox_connection.ipam.ip_addresses.create(
            address=ip_with_prefix,
            status=status,
            description=description,
        )
    
    @classmethod
    def get_roles(cls):
        cls.roles = {
            role.name: role for role in cls.netbox_connection.dcim.device_roles.all()
        }
        logger.debug("Roles retrieved from NetBox API")

    @classmethod
    def get_vms_by_role(cls, role):
        return cls.netbox_connection.virtualization.virtual_machines.filter(
                role_id=role.id
            )

    # Создаем экземпляр устройства netbox
    def __init__(self, site_slug, role, hostname, vlans=None, vm=False, model=None, serial_number=None, ip_address=None, cluster=None) -> None:
        self.hostname = hostname
        self.__site_slug = site_slug
        self.__model = model
        self.__role = role
        self.__serial_number = serial_number
        self.__vlans = vlans
        self.__ip_address = ip_address
        self.__vm = vm
        self.__cluster = cluster
        self.__netbox_device_role = None
        
        # Получение объекта сайта из NetBox
        self.__netbox_site = self.netbox_connection.dcim.sites.get(
            slug=self.__site_slug)
        if not self.__netbox_site:
            self.__critical_error_not_found("site", self.__site_slug)
        # Получение объекта роли устройства из NetBox
        if self.__role:
            self.__netbox_device_role = self.netbox_connection.dcim.device_roles.get(
                name=self.__role)
        # Разрешить работу без роли для ВМ
        if not self.__netbox_device_role and not self.__vm:
            self.__critical_error_not_found("device role", self.__role)

        # Создание/получение устройства или ВМ
        self.__netbox_device = self.__get_or_create_netbox_vm() if vm else self.__get_netbox_device()
        
        # Выбор действия в зависимости от наличия или отсутствия устройства в NetBox
        self.__create_device() if not self.__netbox_device else self.__check_serial_number()

    def __get_or_create_netbox_vm(self):
        self.__netbox_device = self.netbox_connection.virtualization.virtual_machines.get(
            name=self.hostname
        )
        if not self.__netbox_device:
            logger.debug(
                f'Virtual machine {self.__ip_address} not found in NetBox'
            )
            netbox_device = self.netbox_connection.dcim.devices.get(
                name=self.__ip_address
            )
            if netbox_device:
                raise Error(
                    f'There is device with IP address {self.__ip_address} in NetBox'
                )
            logger.info(
                f'Creating virtual machine {self.__ip_address} in NetBox...'
            )
            self.__netbox_device = self.netbox_connection.virtualization.virtual_machines.create(
                name=self.hostname,
                site=self.__netbox_site.id,
                status="active",
                cluster=self.__cluster,
            )
        return self.__netbox_device
    
    def __get_netbox_device(self):
        device = self.netbox_connection.dcim.devices.get(
            name=self.hostname, site=self.__site_slug)
        return device

    def __check_serial_number(self):
        if self.__serial_number and self.__netbox_device.serial != self.__serial_number:
            self.__netbox_device.serial = self.__serial_number
            self.__netbox_device.save()
            logger.debug(
                f'Serial number {self.__netbox_device.serial} was changed to {self.__serial_number}', self.__ip_address)

    def __critical_error_not_found(self, item_type, item_value):
        error_msg = f"{item_type} {item_value} not found in NetBox."
        raise Error(error_msg, self.__ip_address)

    def __create_device(self):
        
        logger.debug("Creating device...")

        self.__netbox_device_type = self.netbox_connection.dcim.device_types.get(
            model=self.__model)
        if not self.__netbox_device_type:
            self.__critical_error_not_found("device type", self.__model)

        # Создаем устройство в NetBox
        self.__netbox_device = self.netbox_connection.dcim.devices.create(
            name=self.hostname,
            device_type=self.__netbox_device_type.id,
            site=self.__netbox_site.id,
            device_role=self.__netbox_device_role.id,
            status="active",
        )
        # Костыль на случай отсутствия серийного номера
        if self.__serial_number:
            self.__netbox_device.serial = self.__serial_number
            self.__netbox_device.save()

        logger.debug("Device created")

    def __get_netbox_interface(self, interface):
        logger.info(
            f"Checking if interface {interface.name} already exists in NetBox...")
        
        if self.__vm:
            existing_interface = self.netbox_connection.virtualization.interfaces.get(
                name=interface.name, virtual_machine=self.__netbox_device.name
            )
        else:
            existing_interface = self.netbox_connection.dcim.interfaces.get(
                name=interface.name, device=self.__netbox_device.name
            )
        
        if not existing_interface:
            if not interface.type:
                interface.type = "other"
            logger.debug(f"Creating interface {interface.name}...")
            if self.__vm:
                existing_interface = self.netbox_connection.virtualization.interfaces.create(
                    name=interface.name,
                    virtual_machine=self.__netbox_device.id,
                    type=interface.type,
                )
            else:
                existing_interface = self.netbox_connection.dcim.interfaces.create(
                    name=interface.name,
                    device=self.__netbox_device.id,
                    type=interface.type,
                )
        else:
            logger.debug(f"Interface {interface.name} already exists")

        self.__netbox_interface = existing_interface

    def add_interface(self, interface):
        self.__get_netbox_interface(interface)

        if self.__netbox_interface:
            update_fields = ['name', 'mtu', 'mac_address', "description", 'mode']
            for field in update_fields:
                val = getattr(interface, field, None)
                if val is not None:
                    setattr(self.__netbox_interface, field, val)

            self.__netbox_interface.untagged_vlan = next(
                (vlan for vlan in self.__vlans if str(vlan.vid) == interface.untagged), None)
            self.__netbox_interface.tagged_vlans = [
                vlan for vlan_id in interface.tagged or []
                for vlan in self.__vlans
                if str(vlan.vid) == vlan_id
            ]
            self.__netbox_interface.save()

            if hasattr(interface, 'ip_with_prefix'):
                logger.debug(f"Interface {interface.name} has IP address")
                self.__create_ip_address(interface)

    def __create_ip_address(self, interface):
        try:
            def handle_existing_ip(existing_ip):
                # Проверяем совпадает ли префикс у найденного в NetBox ip-адреса
                if existing_ip.address == interface.ip_with_prefix:
                    logger.debug(
                        f"IP address {interface.ip_with_prefix} already exists")
                    if self.__vm:
                        existing_ip.assigned_object_type = "virtualization.vminterface"
                    else:
                        existing_ip.assigned_object_type = "dcim.interface"
                    existing_ip.assigned_object_id = self.__netbox_interface.id
                    existing_ip.save()
                else:
                    # Удаляем ip в NetBox, если префикс не совпал
                    delete_and_create_new_ip(existing_ip)

            def delete_and_create_new_ip(existing_ip):
                logger.debug(f"Deleting IP address {existing_ip}...")
                existing_ip.delete()
                if len(existing_ips) < 2:
                    create_new_ip()
                existing_ips.remove(existing_ip)   # Remove the deleted IP

            def create_new_ip():
                logger.debug(
                    f"Creating IP address {interface.ip_with_prefix}...")
                if self.__vm:
                    return self.netbox_connection.ipam.ip_addresses.create(
                        address=interface.ip_with_prefix,
                        status="active",
                        assigned_object_type="virtualization.vminterface",
                        assigned_object_id=self.__netbox_interface.id,
                    )
                else:
                    return self.netbox_connection.ipam.ip_addresses.create(
                        address=interface.ip_with_prefix,
                        status="active",
                        assigned_object_type="dcim.interface",
                        assigned_object_id=self.__netbox_interface.id,
                    )

            logger.debug(
                f"Checking if IP address {interface.ip_with_prefix} already exists in NetBox...")
            existing_ips = list(self.netbox_connection.ipam.ip_addresses.filter(
                address=interface.ip_address
            ))

            if existing_ips:
                for existing_ip in existing_ips:
                    handle_existing_ip(existing_ip)
            else:
                create_new_ip()

            if interface.ip_address == self.__ip_address:
                if str(self.__netbox_device.primary_ip4) != interface.ip_with_prefix:
                    logger.debug(
                        f"Setting {interface.ip_address} as primary IP address")
                    self.__netbox_device.primary_ip4 = {
                        'address': interface.ip_with_prefix}
                    self.__netbox_device.save()

        except pynetbox.core.query.RequestError as e:
            error_message = f"Request failed for IP address {interface.ip_with_prefix}\n{e}"
            calling_function = inspect.stack()[1].function
            NonCriticalError(
                error_message, interface.ip_with_prefix, calling_function)

    def connect_to_neighbor(self, neighbor_device, interface):
        def recreate_cable():
            logger.debug(f'Deleting the cable...')
            self.__netbox_interface.cable.delete()
            create_cable()

        def create_cable():
            logger.info(f'Creating the cable...')
            try:
                self.__netbox_interface.cable = self.netbox_connection.dcim.cables.create(
                    a_terminations=[{
                        "object_id": self.__netbox_interface.id,
                        "object_type": 'dcim.interface',
                    }],
                    b_terminations=[{
                        "object_id": self.__neighbor_interface.id,
                        "object_type": f'dcim.{interface.kind}',
                    }]
                )
                logger.debug(f'The cable has been created')
            except Exception as e:
                Error(
                    f'Cable connection to {neighbor_device.hostname} {self.__neighbor_interface.name} failed.\n{e}', self.__ip_address)

        def check_and_recreate_cable_if_needed():
            for link_peer in self.__netbox_interface.link_peers:
                # If the cable is connected to another port, delete it and create a new one
                if link_peer.id != self.__neighbor_interface.id:
                    NonCriticalError(
                        f'Кабель включен в другой порт: ({link_peer.device} {link_peer})'
                    )
                    recreate_cable()

        # Mapping between interface kind and corresponding Netbox connection method
        interface_mapping = {
            'interface': self.netbox_connection.dcim.interfaces.get,
            'rearport': self.netbox_connection.dcim.rear_ports.get,
            'frontport': self.netbox_connection.dcim.front_ports.get,
        }

        # Get the neighbor interface based on the kind of interface
        self.__neighbor_interface = interface_mapping[interface.kind](
            device=neighbor_device.hostname,
            name=interface.name if interface.kind == 'interface' else None,
        )

        logger.info(
            f"Checking if cable in {self.__netbox_interface.name} exists...")
        # Если интерфейса хоста нет кабеля - создаем кабель между интерфейсами свича и хостом
        if not self.__netbox_interface.cable:
            create_cable()
        # Если кабель существует, проверяем что он включен в соответсвующий порт свича
        else:
            logger.debug(f'The cable already exists')
            if self.__netbox_interface.link_peers_type:
                # Если сейчас соседский интерфейс dcim.interface
                if self.__netbox_interface.link_peers_type == 'dcim.interface':
                    if self.__netbox_interface.link_peers_type == ('dcim.'+interface.kind):
                        check_and_recreate_cable_if_needed()
                    # Переключать свич или хост в розетку - можно
                    else:
                        logger.info(
                            f'Переключаем устройство в розетку: ({self.__neighbor_interface.device} {self.__neighbor_interface})...'
                        )
                        recreate_cable()
                # Если сейчас соседский интерфейс dcim.rearport
                if self.__netbox_interface.link_peers_type == 'dcim.rearport':
                    # Никогда не отключаем порт свича от розетки в "пустоту"
                    if self.__netbox_interface.link_peers_type == ('dcim.'+interface.kind):
                        check_and_recreate_cable_if_needed()
                # Если сейчас соседский интерфейс dcim.frontport
                if self.__netbox_interface.link_peers_type == 'dcim.frontport':
                    if self.__netbox_interface.link_peers_type == ('dcim.'+interface.kind):
                        check_and_recreate_cable_if_needed()
                    # Переключать хост от розетки в "пустоту" - можно, если при этом меняется порт свича
                    else:
                        for endpoint in self.__netbox_interface.connected_endpoints:
                            if endpoint.id != self.__neighbor_interface.id:
                                logger.info(
                                    f'Отключаем хост от розетки...'
                                )
                                recreate_cable()
            else:
                logger.debug(
                    f'Кабель не включен в соседнее устройство: ({self.__neighbor_interface.device} {self.__neighbor_interface})'
                )
                recreate_cable()

    def get_platform(self, csv_os):
        slug = self.__create_slug(csv_os)
        self.__platform = self.netbox_connection.dcim.platforms.get(
            slug=slug
        )
        if not self.__platform:
            self.__platform = self.netbox_connection.dcim.platforms.create(
                name=csv_os,
                slug=slug,
            )
        self.__netbox_device.platform = self.__platform
        self.__netbox_device.save()

    # Creating URL-friendly unique shorthand
    def __create_slug(self, name):
        return re.sub(r'\W+', '-', name).lower()
