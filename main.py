import ipaddress
import time
import yaml

from datetime import datetime
from datetime import timedelta
from dotenv import load_dotenv

from custom_modules.log import logger
from custom_modules.netbox_connector import NetboxDevice
from custom_modules.error_handling import print_errors
from custom_modules.errors import Error, NonCriticalError
from custom_modules.pfsense import download_config
from custom_modules.keadhcp import KeaDHCP
from custom_modules.mac_vendor_lookup import MacVendorLookup
from custom_modules.windows_dhcp import WindowsDHCP


class Lease:
    def __init__(self, ip_address, start_date, mac_address, vendor_class, hostname):
        # Получение IP-адреса с указанием длины префикса
        self.ip_address = ip_address
        self.netbox_prefix = NetboxDevice.get_prefix_for_ip(ip_address)
        self.ip_with_prefix = f'{ip_address}/{self.netbox_prefix.prefix.split("/")[1]}'

        # If start_date is None, it's a static lease which is always active.
        self.is_static = start_date is None
        self.age = None if self.is_static else self.__calculate_lease_age(start_date)
        if self.is_static:
            self.status = "active"
        else:
            if self.age <= 1:
                self.status = "active"
            elif 1 < self.age <= 30:
                self.status = "reserved"
            elif 30 < self.age <= 365:
                self.status = "dhcp"
            else:
                self.status = "deprecated"
        
        # Формирование description
        self.mac_address = mac_address
        self.vendor_class = vendor_class
        self.hostname = hostname
        self.description = self.__generate_description(start_date)

    def __generate_description(self, start_date):
        start_str = 'Static lease' if self.is_static else f'Lease started on {start_date.split(" ", 1)[1]}'
        mac_str = f' / {self.mac_address if self.mac_address else "unknown mac"}'
        hostname_str = f' / {self.hostname if self.hostname else "unknown hostname"}'
        vendor_str = f' / {self.vendor_class if self.vendor_class else mac_vendor_lookup.get_vendor_by_mac(self.mac_address)}'
        return start_str + mac_str + hostname_str + vendor_str
    

    @staticmethod
    def __calculate_lease_age(start_date):
        start_date = start_date.split(' ', 1)[1]    # строка содержит лишнюю инфу
        start_date = datetime.strptime(start_date, "%Y/%m/%d %H:%M:%S")
        return (datetime.now().date() - start_date.date()).days


def parse_file_with_leases(device):
    file_content = download_config(device)
    logger.info(f'{device.primary_ip.address} downloaded')

    leases_data = file_content.split("lease")
    total_lines = len(leases_data) - 3  # subtracting 3 skipped lines

    leases = []
    # skipping initial part of the file and starting the index from 1
    for i, lease_text in enumerate(leases_data[3:], 1):
        logger.debug('Parsing line... ' + str(i) + '/' + str(total_lines))

        if lease_text.strip():
            # assuming IP address comes after 'lease' keyword
            ip_address = lease_text.split()[0]
            try:
                start_date = lease_text.split("starts")[1].split(";")[0].strip()
            except IndexError:
                start_date = None
            try:
                mac_address = lease_text.split("hardware ethernet")[
                    1].split(";")[0].strip()
            except IndexError:
                mac_address = None
            try:
                vendor_class = lease_text.split(
                    "set vendor-class-identifier =")[1].split(";")[0].strip()
            except IndexError:
                vendor_class = None
            try:
                hostname = lease_text.split(
                    "client-hostname")[1].split(";")[0].strip()
            except IndexError:
                hostname = None

            lease = Lease(ip_address, start_date, mac_address,
                          vendor_class, hostname)
            leases.append(lease)
    return leases

def get_leases_by_kea_api():
    def convert_time(timestamp):
        time_struct = time.localtime(timestamp)
        day_of_week = time_struct.tm_wday
        day_of_week += 1
        formatted_date = time.strftime("%Y/%m/%d %H:%M:%S", time_struct)
        final_output = f'{day_of_week} {formatted_date}'
        return final_output

    # Получение лизов
    granted_leases = keadhcp.lease_get_all()
    static_leases = keadhcp.static_get_all()
    combined_list = granted_leases + static_leases
    
    # # Find IPs in both granted_leases and static_leases
    # static_ips = {lease['ip-address'] for lease in static_leases}
    # duplicates_with_hostnames = [
    #     lease for lease in granted_leases
    #     if lease['ip-address'] in static_ips and lease.get('hostname')
    # ]
    # # Save to a file with the format: ip;mac;hostname
    # with open('duplicates.txt', 'w') as file:
    #     for lease in duplicates_with_hostnames:
    #         line = f"{lease['ip-address']};{lease['hw-address']};{lease['hostname']};\n"
    #         file.write(line)
    # # Log the action
    # logger.debug(f'Saved {len(duplicates_with_hostnames)} duplicated leases with hostnames to duplicates.txt')
    
    # Удаление дубликатов
    seen = set()
    leases = []
    for item in combined_list:
        ip = item['ip-address']
        if ip not in seen:
            seen.add(ip)
            leases.append(item)
    logger.debug(f'{len(leases)} leases received')
    
    processed_leases = []
    for index, lease in enumerate(leases):
        try:
            cltt_time = convert_time(lease['cltt']) if 'cltt' in lease else None
        except Exception as e:
            logger.error(f'Error converting cltt for lease: {e}')
            cltt_time = None
        processed_lease = Lease(lease['ip-address'], cltt_time, lease['hw-address'], None, lease['hostname'])
        processed_leases.append(processed_lease)
        logger.debug(f"Processed lease count: {index + 1}")

    return processed_leases

def process_leases(leases):
    NetboxDevice.create_connection()
    for lease in leases:
        try:
            NetboxDevice.create_ip_address(
                lease.ip_address, lease.ip_with_prefix, status=lease.status, description=lease.description)
        except Error:
            continue

def connect_to_kea_agent():
    services = NetboxDevice.get_services_by_vm(router)
    for service in services:
        if service.description == 'Kea DHCP API':
            api_port = service.ports[0]
            break
    if not api_port:
        logger.error('Kea DHCP API port not found')
        return None
    return KeaDHCP(router.primary_ip.address.split('/')[0], api_port)
    
def clear_leases():
    pools = keadhcp.get_pools()
    for pool in pools:
        start_ip, end_ip = pool.split('-')
        NetboxDevice.remove_ip_range(start_ip, end_ip)
        logger.info(f'{start_ip} - {end_ip} cleared')
    logger.debug(f'{len(pools)} pools found')

def clear_windows_leases(dhcp_server, current_leases):
    """Update leases in Netbox to 'dhcp' status for IPs that are no longer present in current leases."""
    logger.info(f'Updating Windows DHCP leases for server {dhcp_server.server_name} to "dhcp" status...')

    # Получаем множество текущих IP-адресов
    current_ips = {lease.ip_address for lease in current_leases}
    ips_to_update = []
    
    for scope in dhcp_server.scopes:
        start_ip = scope['StartRange']
        end_ip = scope['EndRange']
        ip_list = [str(ipaddress.IPv4Address(ip)) for ip in range(int(ipaddress.IPv4Address(start_ip)), int(ipaddress.IPv4Address(end_ip)) + 1)]
        
        for ip in ip_list:
            # Проверяем, есть ли IP-адрес в текущих лизах
            if ip not in current_ips:
                # Если IP-адрес отсутствует в текущих лизах, добавляем его в список для обновления
                ips_to_update.append(ip)

    for ip in ips_to_update:
        updated = NetboxDevice.update_ip_address(ip, status='dhcp')
        if updated:
            logger.info(f'Updated IP {ip} in Netbox to "dhcp" status')
        else:
            logger.warning(f'Failed to update IP {ip} in Netbox to "dhcp" status')

    logger.info(f'Updated Windows DHCP leases for server {dhcp_server.server_name} to "dhcp" status')


# Загрузка данных из файла настроек
with open('settings.yaml', 'r') as file:
    settings_data = yaml.safe_load(file) or {}

router_settings = settings_data.get('router_settings', {})
routers_to_skip = router_settings.get('skip_routers', []) or []  # Убедимся, что это точно список
kea_dhcps = router_settings.get('kea_dhcp', []) or []           # Убедимся, что это точно список
windows_dhcps = router_settings.get('windows_dhcp', []) or []   # Убедимся, что это точно список

# Загрузка переменных окружения из .env
load_dotenv(dotenv_path='.env')
# Загрузка кэша мак-адресов
mac_vendor_lookup = MacVendorLookup()

# Подключение к Netbox
NetboxDevice.create_connection()
NetboxDevice.get_roles()
router_devices = NetboxDevice.get_vms_by_role(
    role=NetboxDevice.roles['Router'])

for server in windows_dhcps:
    try:
        dhcp_server = WindowsDHCP(server)
        leases = dhcp_server.get_leases(Lease, skip=False)
        clear_windows_leases(dhcp_server, leases)
        process_leases(leases)
    except Exception as e:
        logger.error(f'Error processing Windows DHCP server {server}: {e}')
        continue

for router in router_devices:
    # Skip routers from settings
    if router['name'] in routers_to_skip:
        continue
    if router['name'] in kea_dhcps:
        keadhcp = connect_to_kea_agent()
        leases = get_leases_by_kea_api()
        clear_leases()
    else:
        leases = parse_file_with_leases(router)
    # input(f'{len(leases)} leases found. Press Enter to process {router.name}...')   # for debug only
    process_leases(leases)
mac_vendor_lookup.save_cache()  # Save mac vendor cache
print_errors()
