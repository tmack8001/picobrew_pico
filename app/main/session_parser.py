import json
from datetime import datetime
from pathlib import Path
from flask import current_app

from .config import brew_active_sessions_path, ferm_active_sessions_path, iSpindel_active_sessions_path
from .model import PicoBrewSession, PicoFermSession, iSpindelSession

file_glob_pattern = "[!._]*.json"

active_brew_sessions = {}
active_ferm_sessions = {}
active_still_sessions = {}
active_iSpindel_sessions = {}


def load_session_file(filename):
    json_data = {}
    try:
        with open(filename) as fp:
            raw_data = fp.read().rstrip()
            session = recover_incomplete_session(raw_data, filename)
            json_data = json.loads(session)
    except Exception as e:
        current_app.logger.error("An exception occurred parsing session file {}".format(filename))
        current_app.logger.error(e)

    return json_data


def recover_incomplete_session(raw_data, filename):
    # attempt to recover various incomplete session files
    # (raw_data is already rstrip() so no trailing whitespace)
    recovered_session = raw_data
    if raw_data == None or raw_data.endswith('[') or raw_data == '':
        # aborted session data file (containing no datalog entries)
        recovered_session = '[\n]'
    elif raw_data.endswith(',\n]'):
        # closed trailing comma in datalog
        recovered_session = raw_data[:-3] + '\n]\n'
    elif raw_data.endswith(',\n\n]'):
        # closed trailing comma and extra newline in datalog
        recovered_session = raw_data[:-4] + '\n]\n'
    elif raw_data.endswith('}'):
        # unclosed array of data entries
        recovered_session = raw_data + '\n]\n'
    elif raw_data.endswith(','):
        # open trailing comma
        recovered_session = raw_data[:-1] + '\n]\n'

    return recovered_session


def load_brew_session(file):
    info = file.stem.split('#')

    # 0 = Date, 1 = UID, 2 = RFID / Session GUID (guid), 3 = Session Name, 4 = Session Type (integer - z only)
    json_data = load_session_file(file)

    # unencode the name section ('_' => ' ' ; '%23' -> '#')
    name = info[3].replace('_', ' ').replace("%23", "#")
    step = ''
    chart_id = info[0] + '_' + info[2]
    alias = '' if info[1] not in active_brew_sessions else active_brew_sessions[info[1]].alias

    session_type = None
    if len(info) > 4:
        session_type = int(info[4])

    session = {
        'date': datetime.strptime(info[0], '%Y%m%d_%H%M%S'),
        'name': name,
        'filename': Path(file).name,
        'filepath': Path(file),
        'uid': info[1],
        'session': info[2],
        'is_pico': len(info[1]) == 32,
        'type': session_type,
        'alias': alias,
        'data': json_data,
        'graph': get_brew_graph_data(chart_id, name, step, json_data)
    }
    if len(json_data) > 0 and 'recovery' in json_data[-1]:
        session.update({'recovery': json_data[-1]['recovery']})
    return (session)


def get_brew_graph_data(chart_id, session_name, session_step, session_data, is_pico=None):
    wort_data = []  # Shared

    block_data = []  # Pico and ZSeries Only

    board_data = []  # Zymatic Only
    heat1_data = []  # Zymatic Only
    heat2_data = []  # Zymatic Only

    target_data = []  # ZSeries Only
    drain_data = []  # ZSeries Only
    ambient_data = []  # ZSeries Only
    valve_position = []  # ZSeries Only

    events = []
    plot_bands = []
    for data in session_data:
        if all(k in data for k in ('therm', 'wort')):  # Pico and ZSeries
            wort_data.append([data['time'], int(data['wort'])])
            block_data.append([data['time'], int(data['therm'])])

        if all(k in data for k in ('target', 'drain', 'ambient', 'position')):  # ZSeries
            target_data.append([data['time'], int(data['target'])])
            drain_data.append([data['time'], int(data['drain'])])
            ambient_data.append([data['time'], int(data['ambient'])])
            # TODO figure out how to add `position`, `pause_state` and `error` to the graphs?
            valve_position.append([data['time'], int(data['position'])])

        if all(k in data for k in ('wort', 'board', 'heat1', 'heat2')):  # Zymatic
            wort_data.append([data['time'], int(data['wort'])])
            board_data.append([data['time'], int(data['board'])])
            heat1_data.append([data['time'], int(data['heat1'])])
            heat2_data.append([data['time'], int(data['heat2'])])

        # add an overlay event for each step
        if 'event' in data:
            events.append({
                'color': 'black',
                'value': data['time'],
                'label': {
                    'text': data['event']
                }
            })

        # add an overlay error for each errorCode or pauseReason
        error_code = data['errorCode'] if 'errorCode' in data else 0
        pause_reason = data['pauseReason'] if 'pauseReason' in data else 0
        if error_code != 0 or pause_reason != 0:
            if len(plot_bands) == 0:
                plot_bands.append({
                    'from': data.get('time'),
                    'to': None,
                    'label': {
                        'text': reason_phrase(error_code, pause_reason)
                    }
                })
        elif len(plot_bands) > 0:
            plot_bands[-1].update({'to', data.get('time')})

    # if last data point is pause or error
    if len(plot_bands) > 0 and plot_bands[-1]['to'] == None:
        plot_bands[-1]['to'] = session_data[-1]['time']


    graph_data = {
        'chart_id': chart_id,
        'title': {'text': session_name},
        'subtitle': {'text': session_step},
        'xaplotlines': events,
        'xaplotbands': plot_bands
    }
    if len(ambient_data) > 0:
        graph_data.update({'series': [
            {'name': 'Target', 'data': target_data},
            {'name': 'Wort', 'data': wort_data},
            {'name': 'Heat Exchanger', 'data': block_data},
            {'name': 'Drain', 'data': drain_data},
            {'name': 'Ambient', 'data': ambient_data}
        ]})
    elif len(block_data) > 0 or is_pico:
        graph_data.update({'series': [
            {'name': 'Wort', 'data': wort_data},
            {'name': 'Heat Block', 'data': block_data}
        ]})
    else:
        graph_data.update({'series': [
            {'name': 'Wort', 'data': wort_data},
            {'name': 'Heat Loop (Glycol)', 'data': heat1_data},
            {'name': 'Board', 'data': board_data},
            {'name': 'Heat Loop (Element)', 'data': heat2_data}
        ]})
    return graph_data


def load_ferm_session(file):
    info = file.stem.split('#')

    # 0 = Date, 1 = Device UID
    json_data = load_session_file(file)

    chart_id = info[0] + '_' + info[1]
    name = info[1]
    alias = info[1] if info[1] not in active_ferm_sessions else active_ferm_sessions[info[1]].alias

    return ({
        'uid': info[1],
        'filename': Path(file).name,
        'filepath': Path(file),
        'alias': alias,
        'date': datetime.strptime(info[0], '%Y%m%d_%H%M%S'),
        'name': name,  # should change to brew/user defined session name
        'data': json_data,
        'graph': get_ferm_graph_data(chart_id, None, json_data)
    })


# map programmatic codes to user facing strings (displayed in the brew graph)
#
# error codes:
#   4 == Overheat - too hot
#   6 == Overheat - Max HEX Wort Delta
#  12 == PicoStill Error
#
# pause reasons:
#   1 == waiting for user / finished / program
def reason_phrase(error_code, pause_reason):
    reason = ''
    if pause_reason != 0:
        reason += 'pause: '
        if pause_reason == 1:
            reason += 'program'
    elif error_code != 0:
        reason += 'error: {error_code}'

    return reason


def get_ferm_graph_data(chart_id, voltage, session_data):
    temp_data = []
    pres_data = []
    for data in session_data:
        temp_data.append([data['time'], float(data['temp'])])
        pres_data.append([data['time'], float(data['pres'])])
    graph_data = {
        'chart_id': chart_id,
        'title': {'text': 'Fermentation'},
        'series': [
            {
                'name': 'Temperature',
                'data': temp_data
            }, {
                'name': 'Pressure',
                'data': pres_data,
                'yAxis': 1
            }
        ],
    }
    if voltage:
        graph_data.update({'subtitle': {'text': 'Voltage: ' + voltage}})
    return graph_data


def load_iSpindel_session(file):
    info = file.stem.split('#')
    
    # 0 = Date, 1 = Device UID
    json_data = load_session_file(file)
    
    chart_id = info[0] + '_' + str(info[1])
    alias = info[1] if info[1] not in active_iSpindel_sessions else active_iSpindel_sessions[info[1]].alias
    
    return ({
        'uid': info[1],
        'filename': Path(file).name,
        'filepath': Path(file),
        'alias': alias,
        'date': info[0],
        'name': alias,  # should change to brew/user defined session name
        'data': json_data,
        'graph': get_iSpindel_graph_data(chart_id, None, json_data)
    })


def get_iSpindel_graph_data(chart_id, voltage, session_data):
    temp_data = []
    gravity_data = []
    for data in session_data:
        temp_data.append([data['time'], float(data['temp'])])
        gravity_data.append([data['time'], float(data['gravity'])])
    graph_data = {
        'chart_id': str(chart_id),
        'title': {'text': 'Fermentation'},
        'series': [
            {
                'name': 'Temperature',
                'data': temp_data
            }, {
                'name': 'Specific Gravity',
                'data': gravity_data,
                'yAxis': 1
            }
        ],
    }

    if voltage:
        graph_data.update({'subtitle': {'text': 'Voltage: ' + voltage}})
    return graph_data


def restore_active_brew_sessions():
    if active_brew_sessions == {}:
        active_brew_session_files = list(brew_active_sessions_path().glob(file_glob_pattern))
        for file in active_brew_session_files:
            # print('DEBUG: restore_active_sessions() found {} as an active session'.format(file))
            brew_session = load_brew_session(file)
            # print('DEBUG: restore_active_sessions() {}'.format(brew_session))
            uid = brew_session['uid']
            if uid not in active_brew_sessions:
                active_brew_sessions[uid] = PicoBrewSession()

            session = active_brew_sessions[uid]
            session.file = open(file, 'a')
            session.file.flush()
            session.filepath = file
            session.created_at = brew_session['date']
            session.name = brew_session['name']
            session.type = brew_session['type']
            session.session = brew_session['session']                   # session guid
            session.id = -1                                             # session id (integer)

            if 'recovery' in brew_session:
                session.recovery = brew_session['recovery']             # find last step name

            # session.remaining_time = None
            session.data = brew_session['data']

            active_brew_sessions[uid] = session


def restore_active_ferm_sessions():
    if active_ferm_sessions == {}:
        active_ferm_session_files = list(ferm_active_sessions_path().glob(file_glob_pattern))
        for file in active_ferm_session_files:
            # print('DEBUG: restore_active_sessions() found {} as an active session'.format(file))
            ferm_session = load_ferm_session(file)
            # print('DEBUG: restore_active_sessions() {}'.format(ferm_session))
            uid = ferm_session['uid']
            if uid not in active_ferm_sessions:
                active_ferm_sessions[uid] = PicoFermSession()

            session = active_ferm_sessions[uid]
            session.file = open(file, 'a')
            session.file.flush()
            session.filepath = file
            session.start_time = ferm_session['date']
            session.active = True

            session.uninit = False
            session.data = ferm_session['data']
            session.graph = ferm_session['graph']

            active_ferm_sessions[uid] = session


def restore_active_iSpindel_sessions():
    if active_iSpindel_sessions == {}:
        active_iSpindel_session_files = list(iSpindel_active_sessions_path().glob(file_glob_pattern))
        for file in active_iSpindel_session_files:
            # print('DEBUG: restore_active_sessions() found {} as an active session'.format(file))
            iSpindel_session = load_iSpindel_session(file)
            # print('DEBUG: restore_active_sessions() {}'.format(ferm_session))
            uid = iSpindel_session['uid']
            if uid not in active_iSpindel_sessions:
                active_iSpindel_sessions[uid] = iSpindelSession()

            session = active_iSpindel_sessions[uid]
            session.file = open(file, 'a')
            session.file.flush()
            session.filepath = file
            session.start_time = iSpindel_session['date']
            session.active = True

            session.uninit = False
            session.data = iSpindel_session['data']
            session.graph = iSpindel_session['graph']

            active_iSpindel_sessions[uid] = session

def restore_active_sessions():
    # initialize active sessions during start up
    restore_active_brew_sessions()
    restore_active_ferm_sessions()
    restore_active_iSpindel_sessions()
