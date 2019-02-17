from ansible import errors
import re

def to_rec(arr, fields):
    """ convert array to dictionary, naming records after another array """
    if len(arr) != len(fields):
        raise errors.AnsibleFilterError('to_rec: expected %d fields, got %d' % (len(fields), len(arr)))
    return dict(zip(fields, arr))

class FilterModule(object):
    ''' A filter to convert array into record '''
    def filters(self):
        return {
            'record' : to_rec
        }
