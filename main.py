import os
import sys

from datetime import datetime
from datetime import timedelta
from dotenv import load_dotenv

from dhcp_leases_parser.log import logger
from dhcp_leases_parser.netbox import NetboxDevice
from dhcp_leases_parser.error_handling import print_errors
from dhcp_leases_parser.errors import Error, NonCriticalError
from dhcp_leases_parser.pfsense import download_config


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
        self.description = f'leased {self.age if self.age else "0"} days ago / {self.mac_address if self.mac_address else "unknown mac"} / {self.hostname if self.hostname else "unknown hostname"} / {self.vendor_class if self.vendor_class else "unknown vendor"}'

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


def process_leases(leases):
    for lease in leases:
        try:
            logger.debug(f'{lease.ip_address} handling...')
            NetboxDevice.create_ip_address(
                lease.ip_address, lease.ip_with_prefix, status=lease.status, description=lease.description)
        except Error:
            continue


# Загрузка переменных окружения из .env
load_dotenv(dotenv_path='.env')
NetboxDevice.create_connection()
NetboxDevice.get_roles()
router_devices = NetboxDevice.get_vms_by_role(
    role=NetboxDevice.roles['Router'])
for router in router_devices:
    leases = parse_file_with_leases(router)
    # input(f'{len(leases)} leases found. Press Enter to process {router.name}...')   # for debug only
    process_leases(leases)
print_errors()
