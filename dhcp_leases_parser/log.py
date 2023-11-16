import logging
import sys
from logging.handlers import RotatingFileHandler

# Initialize logger with the name 'dhcp_leases_parser'
logger = logging.getLogger('dhcp_leases_parser')
logger.setLevel(logging.INFO)  # Set logging level to INFO

# Configure Console Handler
c_format = '[%(asctime)s.%(msecs)03d %(module)s - %(funcName)23s() ] %(message)s'
c_handler = logging.StreamHandler(sys.stdout)
c_handler.setFormatter(logging.Formatter(
    c_format, datefmt='%d.%m.%Y %H:%M:%S'))
logger.addHandler(c_handler)

# Configure File Handler
f_format = "[%(asctime)s.%(msecs)03d - %(funcName)23s() ] %(message)s"
# Use underscore for readability in large numbers
f_handler = RotatingFileHandler(
    'dhcp_leases_parser.log', maxBytes=10_000_000, backupCount=5, encoding='utf-8')
f_handler.setFormatter(logging.Formatter(
    f_format, datefmt='%d.%m.%Y %H:%M:%S'))
logger.addHandler(f_handler)
