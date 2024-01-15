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


class Lease:
    def __init__(self, ip_address, start_date, mac_address, vendor_class, hostname):
        # Получение IP-адреса с указанием длины префикса
        self.ip_address = ip_address
        self.netbox_prefix = NetboxDevice.get_prefix_for_ip(ip_address)
        self.ip_with_prefix = f'{ip_address}/{self.netbox_prefix.prefix.split("/")[1]}'

        # Определение статуса айпишника по времени отсутствия в сети
        self.age = self.__calculate_lease_age(start_date)
        self.status = "active" if timedelta(
            days=self.age) < timedelta(days=365) else "deprecated"

        # Формирование description
        self.mac_address = mac_address
        self.vendor_class = vendor_class
        self.hostname = hostname
        self.description = f'The lease started on {start_date.split(' ', 1)[1]} / {self.mac_address if self.mac_address else "unknown mac"} / {self.hostname if self.hostname else "unknown hostname"} / {self.vendor_class if self.vendor_class else "unknown vendor"}'

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

def get_leases_by_kea_api(router):
    def convert_time(timestamp):
        time_struct = time.localtime(timestamp)
        day_of_week = time_struct.tm_wday
        day_of_week += 1
        formatted_date = time.strftime("%Y/%m/%d %H:%M:%S", time_struct)
        final_output = f'{day_of_week} {formatted_date}'
        return final_output
    
    services = NetboxDevice.get_services_by_vm(router)
    for service in services:
        if service.description == 'Kea DHCP API':
            api_port = service.ports[0]
            break
    if not api_port:
        logger.error('Kea DHCP API port not found')
        return None
    
    KeaDHCP.set_kea_agent_address(router.primary_ip.address.split('/')[0], api_port)
    logger.info(f'Connection to Kea-agent {KeaDHCP.url} established')
    leases = KeaDHCP.lease_get_all()
    logger.debug(f'{len(leases)} leases received')
    
    processed_leases = []
    for index, lease in enumerate(leases):
        processed_lease = Lease(lease['ip-address'], convert_time(lease['cltt'] - lease['valid-lft']), lease['hw-address'], None, lease['hostname'])
        processed_leases.append(processed_lease)
        logger.debug(f"Processed lease count: {index + 1}")

    return processed_leases

def process_leases(leases):
    for lease in leases:
        try:
            logger.debug(f'{lease.ip_address} handling...')
            NetboxDevice.create_ip_address(
                lease.ip_address, lease.ip_with_prefix, status=lease.status, description=lease.description)
        except Error:
            continue


# Загрузка данных из файла настроек
with open('settings.yaml', 'r') as file:
    settings_data = yaml.safe_load(file)
router_settings = settings_data.get('router_settings', {})
routers_to_skip = router_settings.get('skip_routers', [])
kea_dhcp = router_settings.get('kea_dhcp', [])

# Загрузка переменных окружения из .env
load_dotenv(dotenv_path='.env')
NetboxDevice.create_connection()
NetboxDevice.get_roles()
router_devices = NetboxDevice.get_vms_by_role(
    role=NetboxDevice.roles['Router'])
for router in router_devices:
    # Skip routers from settings
    if router['name'] in routers_to_skip:
        continue
    if router['name'] in kea_dhcp:
        leases = get_leases_by_kea_api(router)
    else:
        leases = parse_file_with_leases(router)
    # input(f'{len(leases)} leases found. Press Enter to process {router.name}...')   # for debug only
    process_leases(leases)
print_errors()
