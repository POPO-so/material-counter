"""Manual native authentication only. This module never invokes a process."""


def terminal_argv(program, known_hosts):
    if program not in ('ssh', 'sftp') or not known_hosts or '\n' in known_hosts:
        raise ValueError('Use native ssh/sftp with an explicitly verified known_hosts file')
    options = ['-o', 'StrictHostKeyChecking=yes', '-o', 'UpdateHostKeys=no',
               '-o', 'UserKnownHostsFile=' + known_hosts,
               '-o', 'GlobalKnownHostsFile=' + known_hosts,
               '-o', 'NumberOfPasswordPrompts=1', '-o', 'BatchMode=no',
               '-o', 'PreferredAuthentications=keyboard-interactive,password',
               '-o', 'PubkeyAuthentication=no', '-o', 'ConnectTimeout=5',
               '-o', 'ForwardAgent=no', '-o', 'ClearAllForwardings=yes']
    return [program, *(['-tt'] if program == 'ssh' else []), *options, 'xilinx@192.168.2.99']


def require_authenticated_result(returncode, reason, continuation):
    """Offline contract test for manual native program exit; never retries."""
    if returncode != 0:
        raise RuntimeError('Authentication/host-key/sudo failed; stop: ' + reason)
    return continuation()
