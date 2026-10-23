"""Host-only static parser. Mount observations do not authorize B2."""
import re

def _unescape(text):
    return re.sub(r'\\(040|011|012|134)', lambda m: chr(int(m[1], 8)), text)

def parse_boot_mountinfo(text):
    mounts, errors = [], []
    for number, line in enumerate(text.splitlines(), 1):
        try:
            before, after = line.split(' - ', 1)
            left, right = before.split(), after.split()
            if len(left) < 6 or len(right) != 3:
                raise ValueError('invalid field count')
            int(left[0]); int(left[1])
            if _unescape(left[4]) == '/boot':
                opts = left[5].split(',')
                mounts.append({'mount_id':left[0], 'mountpoint':'/boot',
                               'filesystem':right[0], 'mount_options':opts,
                               'super_options':right[2].split(','),
                               'ro': 'ro' in opts and 'rw' not in opts})
        except (ValueError, IndexError):
            errors.append(number)
    # Stacked mounts require manual disambiguation; never guess active mount.
    return {'boot_present':bool(mounts), 'mounts':mounts, 'parse_errors':errors,
            'readonly_precondition_observed':len(mounts)==1 and mounts[0]['ro'] and not errors,
            'b2_authorized':False, 'state':'STOPPED_CANDIDATE_BOOT_UNAPPROVED'}
