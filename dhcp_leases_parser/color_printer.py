from colorama import Fore

from dhcp_leases_parser.log import logger


# Пресеты колорамы
def print_yellow(message):
    logger.warning(f'{Fore.LIGHTYELLOW_EX}{message}{Fore.RESET}')


def print_red(message):
    logger.error(f'{Fore.RED}{message}{Fore.RESET}')
