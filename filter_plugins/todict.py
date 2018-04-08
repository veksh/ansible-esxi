import re
from ansible import errors


def to_dict(rec_arr, key):
    """ convert array of records to dictionary key -> record """
    return {item[key]: item for item in rec_arr}


def to_dict_flat(rec_arr):
    """ convert array of records to dictionary rec[0] -> rec[1] """
    return {item[0]: item[1] for item in rec_arr}


class FilterModule(object):
    ''' A filter to convert list of records into dict of records '''
    def filters(self):
        return {
            'to_dict': to_dict,
            'to_dict_flat': to_dict_flat
        }
