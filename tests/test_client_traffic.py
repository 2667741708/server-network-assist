import datetime as dt
import json
from unittest.mock import patch

import pytest

from server_network_assist.client_traffic import ClientTraffic
from server_network_assist.client_tunnel import transfer_counters
from server_network_assist.client import ClientPanel


def test_delta_persistence_restart_and_subscriptions(tmp_path):
    meter = ClientTraffic(tmp_path)
    meter.sample('a', 'session1', 100, 20, monotonic=10)
    meter.sample('a', 'session1', 300, 40, monotonic=12)
    assert meter.value['subscriptions']['a']['received'] == 300
    assert meter.rates['a']['download_bytes_per_second'] == 100
    restarted = ClientTraffic(tmp_path)
    restarted.sample('a', 'session1', 350, 50, monotonic=20)
    assert restarted.summary('a')['total_bytes'] == 400
    assert restarted.rates['a']['download_bytes_per_second'] is None
    restarted.sample('b', 'session2', 1000, 10)
    assert restarted.summary('a')['total_bytes'] == 400
    assert restarted.summary('b')['total_bytes'] == 1010


def test_reset_new_session_and_duplicate_do_not_double_count(tmp_path):
    meter = ClientTraffic(tmp_path)
    meter.sample('a', 's1', 100, 50, monotonic=1)
    meter.sample('a', 's1', 100, 50, monotonic=3)
    assert meter.summary('a')['total_bytes'] == 150
    meter.sample('a', 's1', 20, 10, monotonic=5)
    assert meter.summary('a')['total_bytes'] == 180
    assert meter.rates['a']['download_bytes_per_second'] is None
    meter.sample('a', 's2', 500, 10, monotonic=7)
    assert meter.summary('a')['total_bytes'] == 690


def test_midnight_local_day_and_stale_rates(tmp_path):
    meter = ClientTraffic(tmp_path)
    midnight = dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    meter.sample('a', 's', 100, 50, now=midnight - 1, monotonic=1)
    meter.sample('a', 's', 200, 80, now=midnight + 1, monotonic=3)
    assert meter.summary('a')['today_bytes'] == 130
    assert meter.summary('a', active=True)['download_bytes_per_second'] is None
    assert meter.summary('a', active=False)['download_bytes_per_second'] == 0


def test_corrupt_file_not_overwritten_and_save_failure_not_raised(tmp_path):
    path = tmp_path / 'client-traffic.json'
    path.write_text('{broken')
    meter = ClientTraffic(tmp_path)
    meter.sample('a', 's', 100, 20)
    assert path.read_text() == '{broken'
    assert meter.summary('a')['error']
    path.unlink()
    meter = ClientTraffic(tmp_path)
    with patch.object(meter, '_save', side_effect=PermissionError('locked')):
        meter.sample('a', 's', 100, 20)
    assert meter.summary('a')['error']


def test_private_counter_read_refuses_unowned_interface(tmp_path):
    (tmp_path / 'customer-owned-tunnel.json').write_text(json.dumps({'tunnel': 'sna0123456789ab'}))
    with patch('server_network_assist.client_tunnel.subprocess.run') as run:
        with pytest.raises(ValueError):
            transfer_counters('wg-management', tmp_path)
        run.assert_not_called()


def test_transfer_public_fields_only_and_hidden_process(tmp_path):
    (tmp_path / 'customer-owned-tunnel.json').write_text(json.dumps({'tunnel': 'sna0123456789ab'}))
    with patch('server_network_assist.client_tunnel.subprocess.run') as run:
        run.return_value.returncode = 0
        run.return_value.stdout = 'publickey\t100\t20\n'
        assert transfer_counters('sna0123456789ab', tmp_path) == (100, 20)
        assert run.call_args.args[0][-1] == 'transfer'
        assert run.call_args.kwargs['timeout'] == 2


def test_telemetry_failure_cannot_block_exit(tmp_path):
    panel = ClientPanel(tmp_path)
    panel.save_active('grant', 'sna0123456789ab', 'online')
    with patch.object(panel.online, 'configured', return_value=True), \
         patch.object(panel.online, '_record', side_effect=OSError('telemetry failure')), \
         patch.object(panel.online, 'release'), \
         patch('server_network_assist.client_tunnel.stop') as stop, \
         patch('server_network_assist.client_original_network.restore') as restore, \
         patch('server_network_assist.client_app_routing.restore'):
        panel.leave_network()
        stop.assert_called_once()
        restore.assert_called_once()
        assert panel.active() is None
