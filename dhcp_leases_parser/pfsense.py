import os

import paramiko

from log import logger
from errors import Error, NonCriticalError

def download_config(device):
    ip = device.primary_ip.address.split('/')[0]
    port = 22
    try:
        logger.debug(f"Trying to connect to {ip}:{port}!")
        with paramiko.Transport((ip, port)) as transport:
            transport.connect(username=os.getenv('PFSENSE_LOGIN'), password=os.getenv('PFSENSE_PASSWORD'))
            with paramiko.SFTPClient.from_transport(transport) as sftp:
                return sftp.file('/var/dhcpd/var/db/dhcpd.leases', 'r').read().decode('UTF-8')
    except paramiko.AuthenticationException:
        raise Error(f"Authentication failed", ip)
