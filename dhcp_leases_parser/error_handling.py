from prettytable import PrettyTable
from errors import Error, NonCriticalError
from log import logger

def print_errors():
    # Merge the error messages into a single list
    all_error_messages = Error.error_messages + NonCriticalError.error_messages

    # Flatten the list of dictionaries into a single dictionary
    merged_error_messages = {k: v for d in all_error_messages for k, v in d.items()}

    logger.info(f'The work is completed')
    
    # Print errors in a PrettyTable
    if merged_error_messages:
        table = PrettyTable(["IP", "Error"])
        table.align["IP"] = "l"
        table.align["Error"] = "l"
        table.max_width = 75
        table.valign["Error"] = "t"

        for ip, error_message in merged_error_messages.items():
            table.add_row([ip, error_message])

        print(table)
