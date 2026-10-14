import os
import re
import sys
import json
import math
import time
import pickle
import os.path
import ipaddress
import traceback
import subprocess
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox
from concurrent.futures import ThreadPoolExecutor
from Pmw import Balloon
from datetime import datetime
from puresnmp import walk, get

SNMP_COMMUNITY = 'public'
SNMP_TIMEOUT = 2
PRINTERS_FILE = 'Printers.json'
LEGACY_PRINTERS_FILE = 'Printers.pkl'

#Toner colors we draw bars for, matched against the SNMP supply name.
#Anything else (waste toner, drums, fusers...) is skipped
INK_COLORS = ['black', 'cyan', 'magenta', 'yellow']

def resource_path(relative_path):
    #Get absolute path to resource, works for dev and for
    #PyInstaller - Found on stackoverflow
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath('.')
    return os.path.join(base_path, relative_path)

def open_file(path):
    #Open a file with the OS default application
    path = os.path.abspath(path)
    if os.name == 'nt':
        os.startfile(path)
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', path])
    else:
        subprocess.Popen(['xdg-open', path])

def frame_name(printer):
    #Tk widget names can't start with a capital letter or contain '.',
    #so preface with 'a' and replace anything unusual
    return 'a' + re.sub(r'[^\w-]', '_', printer['Name'])

def normalize_printer(printer):
    return {'IP': str(printer.get('IP', '')).strip(),
            'Name': str(printer.get('Name', '')).strip(),
            'Serial': str(printer.get('Serial', '')).strip(),
            'EID': str(printer.get('EID', '')).strip(),
            'Default': bool(printer.get('Default', False))}

def save_printers(printers):
    with open(PRINTERS_FILE, 'w') as f:
        json.dump(printers, f, indent=2)

def load_printers(default_printers):
    #Prefer the JSON printer list, then migrate an old pickle file,
    #then fall back to the defaults
    try:
        with open(PRINTERS_FILE) as f:
            return [normalize_printer(p) for p in json.load(f)]
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    try:
        with open(LEGACY_PRINTERS_FILE, 'rb') as f:
            printers = [normalize_printer(p) for p in pickle.load(f)]
    except Exception:
        printers = [dict(p) for p in default_printers]
    save_printers(printers)
    return printers

def parse_paper_size(dim1, dim2):
    # Sort them so orientation (landscape vs portrait feed) doesn't matter
    dims = sorted([dim1, dim2])

    # Check for standard sizes based on 1/10000 of an inch
    if abs(dims[0] - 85000) < 1000 and abs(dims[1] - 110000) < 1000:
        return "8.5x11"
    elif abs(dims[0] - 85000) < 1000 and abs(dims[1] - 140000) < 1000:
        return "8.5x14"
    elif abs(dims[0] - 110000) < 1000 and abs(dims[1] - 170000) < 1000:
        return "11x17"
    else:
        # Fallback calculation if it's a unique/custom size
        w = round(dims[0] / 10000, 1)
        h = round(dims[1] / 10000, 1)
        return f"{w}x{h}"

def decode(value):
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    return str(value)

def fetch_printer_data(ip, app):
    #Gather everything we display for a printer over SNMP. This runs in a
    #worker thread, so it must not touch any Tk objects
    def values(oid):
        return [item[1] for item in walk(ip, SNMP_COMMUNITY, oid,
                                         timeout=SNMP_TIMEOUT)]

    model = decode(get(ip, SNMP_COMMUNITY, app.model_OID, timeout=SNMP_TIMEOUT))
    alerts = [decode(v) for v in values(app.error_base_OID)]

    #Match supplies to colors by name rather than by position, since
    #printers report differing numbers of supplies (waste toner, drums...)
    supply_levels = values(app.ink_levels_base_OID)
    supply_max = values(app.supply_max_base_OID)
    supply_names = [decode(v).lower() for v in values(app.supply_names_base_OID)]
    if not supply_names:
        #Old Ricoh layout: black, waste toner, cyan, magenta, yellow
        supply_names = ['black', 'waste', 'cyan', 'magenta', 'yellow']
    supply_max += [-2] * (len(supply_levels) - len(supply_max))
    supplies = []
    for name, level, max_level in zip(supply_names, supply_levels, supply_max):
        color = next((c for c in INK_COLORS if c in name), None)
        if color is None or 'waste' in name:
            continue
        if level < 0:
            #-2 = unknown, -3 = 'some remaining' per the Printer MIB
            percent = None
        elif max_level > 0:
            percent = min(100, round(level * 100 / max_level))
        else:
            #Capacity unknown, assume the level is already a percentage
            percent = min(100, level)
        supplies.append((color, percent))

    raw_tray_names = [decode(v) for v in values(app.tray_names_base_OID)]
    tray_current = values(app.tray_current_capacity_base_OID)
    tray_max = values(app.tray_max_capacity_base_OID)
    tray_feeds = values(app.tray_feed_dim_OID)
    tray_xfeeds = values(app.tray_xfeed_dim_OID)
    trays = []
    for i, (name, current, maximum) in enumerate(
            zip(raw_tray_names, tray_current, tray_max)):
        name = name.replace('Paper', '').replace('Tray 3 (LCT)', 'LCT').strip()
        if name == 'Bypass Tray':
            continue
        try:
            if tray_feeds[i] <= 0 or tray_xfeeds[i] <= 0:
                raise ValueError
            size = parse_paper_size(tray_feeds[i], tray_xfeeds[i])
        except (IndexError, ValueError):
            size = 'Unknown'
        trays.append((name, current, maximum, size))

    return {'model': model,
            'alerts': alerts,
            'supplies': supplies,
            'trays': trays,
            'has_lct': any('LCT' in name for name in raw_tray_names)}

class MainApplication(tk.Frame):
    def __init__(self, parent, *args, **kwargs):
        tk.Frame.__init__(self, parent, *args, **kwargs)
        self.parent = parent

        #Define printer names and IP address
        self.default_printers=[
                  {'IP':'172.18.181.227','Name':'CI-121',
                   'Serial':'C068C400217','EID':'14072973','Default': True},
                  {'IP':'172.18.181.228','Name':'CI-202',
                   'Serial':'C068C300002','EID':'14072974','Default': True},
                  {'IP':'172.18.181.232','Name':'CI-214',
                   'Serial':'C758M520307','EID':'14072971','Default': False},
                  {'IP':'172.18.181.244','Name':'CI-301',
                   'Serial':'C728M810465','EID':'14143593','Default': False},
                  {'IP':'172.18.181.231','Name':'CI-335',
                   'Serial':'C068C400148','EID':'14072972','Default': True},
                  {'IP':'172.18.181.230','Name':'CI-DO',
                   'Serial':'C758M520011','EID':'14072970','Default': True},
                  {'IP':'172.18.178.120','Name':'SDW-FL2',
                   'Serial':'C758M520012','EID':'14072977','Default': False},
                  {'IP':'172.18.177.204','Name':'ANX-A',
                   'Serial':'C068C400209','EID':'14072979','Default': True},
                  {'IP':'172.19.55.10','Name':'ANX-B',
                   'Serial':'C068C400222','EID':'14072976','Default': True},
                  {'IP':'172.18.186.18','Name':'RH-204',
                   'Serial':'C727M810074','EID':'14381339','Default': False},
        ]

        self.printers = load_printers(self.default_printers)

        #SNMP results fetched ahead of time, keyed by printer name
        self.prefetched = {}

        #Define OIDs for Ricoh brand printers
        self.model_OID = '.1.3.6.1.2.1.43.5.1.1.16.1'
        self.supply_names_base_OID = '.1.3.6.1.2.1.43.11.1.1.6.1'
        self.supply_max_base_OID = '.1.3.6.1.2.1.43.11.1.1.8.1'
        self.ink_levels_base_OID = '.1.3.6.1.2.1.43.11.1.1.9.1'
        self.tray_names_base_OID = '.1.3.6.1.2.1.43.8.2.1.13'
        self.tray_max_capacity_base_OID = '.1.3.6.1.2.1.43.8.2.1.9.1'
        self.tray_current_capacity_base_OID = '.1.3.6.1.2.1.43.8.2.1.10.1'
        self.error_base_OID = '.1.3.6.1.2.1.43.18.1.1.8.1'
        self.tray_feed_dim_OID = '.1.3.6.1.2.1.43.8.2.1.4'
        self.tray_xfeed_dim_OID = '.1.3.6.1.2.1.43.8.2.1.5'

        #Create printer images
        def image(file_name):
            return tk.PhotoImage(
                file=resource_path('images/' + file_name)).subsample(4, 4)
        self.c6503 = image('c6503.png')
        self.c6503f = image('c6503f.png')
        self.no_connection = image('NoConnection.png')
        self.missing_model = image('missing.png')
        self.model_images = {
            'MP C6004ex': image('c6004ex.png'),
            'MP C3504ex': image('c3504ex.png'),
            'IM C4500': image('c4500.png'),
            'IM C6010': image('c6010.png'),
            'IM C3510': image('c3510.png'),
            'HP Color LaserJet MFP 5800': image('mfp5800.png'),
            'HP Color LaserJet MFP M681': image('mfpm681.png'),
        }

        #Create styles for progress bars
        self.style=ttk.Style()
        self.style.theme_use('alt')
        self.styles={}
        for color in INK_COLORS:
            style_name = color + '.Horizontal.TProgressbar'
            self.style.configure(style_name, background=color)
            self.styles[color] = (style_name, color.capitalize())

        #Define window properties
        root.title('Ricoh Resource Monitor v3.6.3')
        if 'nt' == os.name:
            root.iconbitmap(resource_path('images/icon.ico'))

        #Grab the current time for logfile creation
        self.current_time=datetime.now().strftime('%Y-%m-%d_%H-%M')

        #Create variables to hold current paper deficit
        self.deficit=tk.IntVar()
        self.deficit.set(0)

        self.reams=tk.IntVar()
        self.reams.set(0)

        #One shared tooltip manager for every printer frame
        self.balloon = Balloon(parent)

        #Create frame that will hold the printers
        self.item_frame=ItemFrame(self)
        self.item_frame.pack(side='right', fill='y')

        #Create frame that will hold the checkbuttons
        self.selection_pane=SelectionPane(self)
        self.selection_pane.pack(side='left', fill='y', expand=True,
                                 padx=5, pady=5)

        #Create the hover label in the bottom right
        self.q=tk.Label(self.item_frame, text='?', bd=0,
                        font=(None, 6))
        self.q.pack(side=tk.RIGHT, anchor=tk.S)
        self.balloon.bind(self.q, 'Другого дома нет!')

    def update_totals(self):
        #Recompute the paper deficit from the printer frames currently shown
        total = sum(frame.printer_deficit
                    for frame in self.item_frame.winfo_children()
                    if isinstance(frame, PrinterFrame))
        self.deficit.set(total)
        self.reams.set(math.ceil(total/500))

class SelectionPane(tk.Frame):
    def __init__(self, parent, *args, **kwargs):
        tk.Frame.__init__(self, parent, *args, **kwargs,
                          padx=5, pady=5, borderwidth=2, relief=tk.RIDGE)
        self.parent = parent

        #Define variables for variable refresh
        self.active_ID = None
        self.time_input = None
        self.busy = False

        #Create menu bar
        self.menubar = tk.Menu(self)

        self.filemenu = tk.Menu(self.menubar, tearoff=0)
        self.filemenu.add_command(label='Refresh',
                                  command=lambda:self.set_timeInput())
        self.filemenu.add_command(label='Edit printer list',
                                  command=lambda:self.edit_printer_list())
        self.filemenu.add_separator()
        self.filemenu.add_command(label='Exit', command=lambda:root.destroy())
        self.menubar.add_cascade(label='File', menu=self.filemenu)

        self.aboutmenu = tk.Menu(self.menubar, tearoff=0)
        orig_github_url='https://github.com/4rm/Ricoh-Resource-Monitor/'
        new_github_url='https://github.com/vmp134/Ricoh-Resource-Monitor/'
        self.aboutmenu.add_command(label='Original GitHub',
                                   command=lambda aurl=orig_github_url:webbrowser.open(aurl))
        self.aboutmenu.add_command(label='Current GitHub',
                                   command=lambda aurl=new_github_url:webbrowser.open(aurl))
        self.aboutmenu.add_separator()
        self.aboutmenu.add_command(label='About',
                                   command=lambda:self.about_info())
        self.menubar.add_cascade(label='Help', menu=self.aboutmenu)

        root.config(menu=self.menubar)

        #Create frame for variable refresh options/prompt
        self.reload_frame=tk.Frame(self)
        self.reload_frame_2=tk.Frame(self)
        self.reload_prompt_1=tk.Label(self.reload_frame, text='Reload every ')
        self.reload_prompt_1.pack(side=tk.LEFT)
        self.reload_time=tk.Entry(self.reload_frame, width=3, insertontime=0)
        self.reload_time.pack(side=tk.LEFT)
        self.reload_prompt_2=tk.Label(self.reload_frame, text='seconds')
        self.reload_prompt_2.pack(side=tk.LEFT)
        self.reload_time.insert(tk.END, 600)
        self.reload_time.bind('<Return>', lambda event:self.set_timeInput())
        self.stop_button=tk.Button(self.reload_frame_2, text='Stop',
                                   command=lambda:self.reset_timer(),
                                   width=9, cursor='hand2')
        self.stop_button.pack(side=tk.RIGHT)
        self.refresh_button=tk.Button(self.reload_frame_2, text='Set',
                                      command=lambda:self.set_timeInput(),
                                      width=9, cursor='hand2')
        self.refresh_button.pack(side=tk.LEFT)
        self.reload_frame_2.pack(side=tk.BOTTOM, anchor=tk.E, fill=tk.X)
        self.reload_frame.pack(side=tk.BOTTOM, anchor=tk.W, pady=(10,5))

        #Create frame for suggested ream count
        self.ream_frame=tk.Frame(self)
        self.ream_label=tk.Label(self.ream_frame,
                                 text='Suggested reams: ',
                                 font=(None, 9, 'italic'))
        self.ream_label.pack(side=tk.LEFT)
        self.ream_count=tk.Label(self.ream_frame,
                                 textvariable=parent.reams)
        self.ream_count.pack(side=tk.RIGHT)
        self.ream_frame.pack(anchor=tk.W, fill=tk.BOTH, side=tk.BOTTOM)

        #Create frame for current paper deficit
        self.deficit_frame=tk.Frame(self)
        self.deficit_label=tk.Label(self.deficit_frame,
                                    text='Current deficit: ',
                                    font=(None, 9, 'italic'))
        self.deficit_label.pack(side=tk.LEFT)
        self.total_deficit=tk.Label(self.deficit_frame,
                                    textvariable=parent.deficit)
        self.total_deficit.pack(side=tk.RIGHT)
        self.deficit_frame.pack(anchor=tk.W,pady=(10,0),
                                fill=tk.BOTH,side=tk.BOTTOM)

        #Create frame to hold 'all' and 'none' buttons
        self.option_buttons=tk.Frame(self)
        self.none_check=tk.Checkbutton(self.option_buttons, text='None',
                                      command=lambda:self.none(),
                                       cursor='hand2')
        self.all_check=tk.Checkbutton(self.option_buttons, text='All',
                                     command=lambda:self.all(),
                                      cursor='hand2')
        self.none_check.pack(side=tk.RIGHT)
        self.all_check.pack(side=tk.LEFT)

        self.spawn_checks()

        #Pack options below other checkbuttons
        self.option_buttons.pack(side=tk.BOTTOM)

    def prefetch(self, printers, message=None):
        #Query several printers at once in background threads so offline
        #printers time out in parallel instead of one after another
        self.busy = True
        loading_window = None
        if message:
            loading_window = tk.Toplevel(root)
            loading_window.attributes('-topmost', 1)
            loading_window.title('Loading...')
            if 'nt' == os.name:
                loading_window.iconbitmap(resource_path('images/icon.ico'))
            loading_window.lift()
            logo_canvas=tk.Canvas(loading_window, width=300, height=180)
            logo_canvas.pack()
            self.logo=tk.PhotoImage(file=resource_path('images/icon.gif'))
            logo_canvas.create_image(150, 90, image=self.logo)
            tk.Label(loading_window, text=message,
                     font=(None,10)).pack(pady=(0,10))
        try:
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = {printer['Name']: pool.submit(fetch_printer_data,
                                                        printer['IP'],
                                                        self.parent)
                           for printer in printers}
                while not all(f.done() for f in futures.values()):
                    #Keep the window responsive while we wait
                    root.update()
                    time.sleep(0.05)
            for name, future in futures.items():
                self.parent.prefetched[name] = (future.exception()
                                                or future.result())
        finally:
            if loading_window is not None:
                loading_window.destroy()
            self.busy = False

    def spawn_checks(self):
        #Remove any printer frames left over from a previous printer list
        for frame in self.parent.item_frame.winfo_children():
            if isinstance(frame, PrinterFrame):
                frame.destroy()
        self.parent.prefetched.clear()
        self.parent.update_totals()

        #Initialize list to hold checkbox variables and checkbutton objects
        self.checklist=[]
        try:
            self.printer_check_frame.destroy()
        except AttributeError:
            pass
        self.printer_check_frame = tk.Frame(self)
        self.printer_check_frame.pack(anchor=tk.NW, side=tk.TOP)

        defaults = [entry for entry in self.parent.printers if entry['Default']]
        if defaults:
            self.prefetch(defaults, 'Establishing connection to ' +
                          str(len(defaults)) + ' printer(s)...')

        #For each defined printer, generate a Checkbutton
        for entry in self.parent.printers:
            var=tk.IntVar()
            self.check=tk.Checkbutton(self.printer_check_frame,
                                      text=entry['Name'], variable=var,
                                      command=lambda var=var,entry=entry:
                                          self.spawn_despawn(var.get(), entry),
                                      cursor='hand2')
            self.checklist.append([self.check, var, entry])
            self.check.pack(anchor=tk.W)
            #If the printer is one that we refill on our paper route, autoload
            if entry['Default'] == True:
                self.check.invoke()

    def spawn_despawn(self, status, printer):
        self.none_check.deselect()
        print(printer['Name'] + ' toggled ' + str(status))
        if status==1:
            #If checkbox is checked, spawn printer frame (this replaces any
            #existing frame with the same name)
            self.printer_instance=PrinterFrame(self.parent.item_frame, printer)
            self.printer_instance.pack(side=tk.LEFT, fill=tk.Y)
        elif status==0:
            #If unchecked, destroy printer frame
            self.all_check.deselect()
            frame = self.parent.item_frame.children.get(frame_name(printer))
            if frame is not None:
                frame.destroy()
        self.parent.update_totals()

    def none(self):
        for item in self.checklist:
            item[0].select()
            item[0].invoke()

    def all(self):
        if self.busy:
            return
        self.prefetch([item[2] for item in self.checklist],
                      'Loading all printers...')
        for item in self.checklist:
            item[0].deselect()
            item[0].invoke()

    def refresh(self):
        if not self.busy:
            checked = [item for item in self.checklist if item[1].get()==1]
            self.prefetch([item[2] for item in checked])
            for item in checked:
                item[0].deselect()
                item[0].invoke()
        self.active_ID = self.after(self.time_input*1000, self.refresh)

    def set_timeInput(self):
        reset_interval=self.reload_time.get().strip()
        if self.active_ID is not None:
            #if there's a current 'after' process, cancel it
            self.after_cancel(self.active_ID)
            self.active_ID=None
        if not reset_interval.isdigit():
            #reset to default on non-numeric input
            self.reload_time.delete(0, tk.END)
            self.reload_time.insert(tk.END, 600)
        elif int(reset_interval)>0:
            self.time_input=int(reset_interval)
            self.refresh()

    def reset_timer(self):
        self.reload_time.delete(0, tk.END)
        self.reload_time.insert(tk.END, 0)
        if self.active_ID is not None:
            self.after_cancel(self.active_ID)
            self.active_ID=None

    def edit_printer_list(self):
        self.printer_edit_window = tk.Toplevel(root)
        self.printer_edit_window.attributes('-topmost', 1)
        self.printer_edit_window.title('Edit printer list')
        if 'nt' == os.name:
            self.printer_edit_window.iconbitmap(resource_path('images/icon.ico'))
        self.printer_edit_window.lift()

        self.instruction_frame = tk.Frame(self.printer_edit_window)
        self.instruction_frame.pack(anchor=tk.W, side=tk.TOP)
        edit_prompt = tk.Label(self.instruction_frame,
                               text='Enter printer information',
                               font=(None, 14, 'bold'))
        edit_prompt.pack(side=tk.TOP, anchor=tk.W)
        edit_guide = tk.Label(self.instruction_frame,
                              text='IP,Name,Serial(Optional),EID(Optional),Default',
                              font=(None, 9))
        edit_guide.pack(side=tk.BOTTOM, anchor=tk.W)

        self.edit_pane = tk.Text(self.printer_edit_window, width=70, height=15)
        self.edit_pane.pack(fill=tk.BOTH, expand=True)

        #autopopulate with current list
        self.write_printers(self.parent.printers)

        button_frame = tk.Frame(self.printer_edit_window)
        button_frame.pack(fill=tk.X, side=tk.BOTTOM)
        apply_button = tk.Button(button_frame, text='Apply',
                                 command=lambda:self.apply_printer_list(),
                                 pady=5, height=2)
        apply_button.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        reset_button = tk.Button(button_frame, text='Reset Field',
                                 command=lambda:self.write_printers(self.parent.default_printers),
                                 pady=5, height=2)
        reset_button.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

    def apply_printer_list(self):
        printers = []
        errors = []
        names = set()
        lines = self.edit_pane.get('1.0',tk.END).splitlines()
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            fields = [field.strip() for field in line.split(',')]
            #Serial, EID and Default are optional
            fields += [''] * (5 - len(fields))
            ip, name, serial, eid, default = fields[:5]
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                errors.append('Line ' + str(line_number) +
                              ': "' + ip + '" is not a valid IP address')
                continue
            if not name:
                errors.append('Line ' + str(line_number) + ': missing name')
                continue
            if name in names:
                errors.append('Line ' + str(line_number) +
                              ': duplicate name "' + name + '"')
                continue
            names.add(name)
            printers.append({'IP': ip, 'Name': name, 'Serial': serial,
                             'EID': eid, 'Default': self.return_bool(default)})
        if errors or not printers:
            #Keep the user's edits so they can fix them
            messagebox.showerror('Invalid printer list',
                                 '\n'.join(errors) or 'No printers entered',
                                 parent=self.printer_edit_window)
            return
        self.parent.printers = printers
        save_printers(printers)
        self.printer_edit_window.destroy()
        self.spawn_checks()

    def write_printers(self, printers_dict):
        self.edit_pane.delete('1.0',tk.END)
        for printer in printers_dict:
            self.edit_pane.insert(tk.END, ','.join([printer['IP'],
                                                    printer['Name'],
                                                    printer['Serial'],
                                                    printer['EID'],
                                                    str(printer['Default'])]) + '\n')

    def about_info(self):
        about_popup=tk.Toplevel()
        about_popup.attributes('-topmost', 1)
        about_popup.wm_title('About')
        if 'nt' == os.name:
            about_popup.iconbitmap(resource_path('images/icon.ico'))
        about_popup.lift()
        program_name=tk.Label(about_popup,
                              text='Ricoh Resource Monitor v3.6.3',
                              font=(None,14))
        program_name.pack()
        logo_canvas=tk.Canvas(about_popup, width=300, height=180)
        logo_canvas.pack()
        self.logo=tk.PhotoImage(file=resource_path('images/icon.gif'))
        logo_canvas.create_image(150, 90, image=self.logo)
        tagline=tk.Label(about_popup,
                         text='Developed by Emilio Garcia\nMaintained by Victor Peng\nSC&I IT Helpdesk\nRutgers University')
        tagline.pack(pady=(0,15))

    def return_bool(self, text_input):
        #convert text input to bool
        return text_input.strip().lower() in ['true', 't', 'yes', 'y', '1']

class ItemFrame(tk.Frame):
    def __init__(self, parent, *args, **kwargs):
        tk.Frame.__init__(self, parent, *args, **kwargs)
        self.parent = parent
        #Create a frame object that inherits variables from MainApplication

class PrinterFrame(tk.Frame):
    def __init__(self, parent, printer):
        tk.Frame.__init__(self, parent, name=frame_name(printer), padx=5)

        self.parent = parent
        self.IP=printer['IP']
        self.printer_deficit=0
        app = parent.parent

        try:
            #Use data fetched in the background if we have it
            data = app.prefetched.pop(printer['Name'], None)
            if data is None:
                data = fetch_printer_data(printer['IP'], app)
            elif isinstance(data, Exception):
                raise data
            self.draw(printer, data, app)
        except Exception as err:
            for child in self.winfo_children():
                child.destroy()
            self.printer_deficit=0
            self.draw_error(printer, err, app)

    def draw(self, printer, data, app):
        default_bg = root.cget('bg')
        #grab the default background color to create pseudo-transparent object
        balloon = app.balloon

        printer_name=tk.Label(self, text=printer['Name'], font=(None, 14))
        balloon.bind(printer_name, 'Serial: ' + printer['Serial'] +
                     '\nEID: ' +printer['EID'])
        printer_name.pack()

        printer_model=tk.Label(self, text=data['model'])
        printer_model.pack()

        url='http://' + printer['IP']
        printer_IP=tk.Label(self, text=printer['IP'], fg='blue',
                           font=(None, 8, 'underline'), cursor='hand2')
        balloon.bind(printer_IP, 'Go to printer control panel')
        printer_IP.bind('<Button-1>',
                        lambda event,aurl=url:webbrowser.open(aurl))
        #Bind event property to label to create a hyperlink
        printer_IP.pack()

        alert_frame=tk.Frame(self)
        alert_frame.pack()

        scrollbar=tk.Scrollbar(alert_frame)
        alerts_list=tk.Listbox(alert_frame, height=3, width=30, fg='red',
                              relief='flat', bg=default_bg, activestyle='none',
                              borderwidth=0, selectbackground=default_bg,
                              highlightthickness=0, selectforeground='red')
        alerts_list.pack(side=tk.LEFT, pady=(5,0))
        for alert in data['alerts']:
            alerts_list.insert(tk.END, alert)
        if len(data['alerts'])>3:
            #Only pack the scrollbar if there are more than 3 messages
            #The scrollbar must be at least 3 lines long, so this is the
            #smallest possible size
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            alerts_list.config(yscrollcommand=scrollbar.set)
            scrollbar.config(command=alerts_list.yview)

        #Find the correct picture to match the model
        if data['model'] == 'MP C6503':
            #The C6503 may or may not have an LCT
            printer_image=app.c6503f if data['has_lct'] else app.c6503
        else:
            #If user added model does not have an image, use a placeholder
            printer_image=app.model_images.get(data['model'],
                                               app.missing_model)

        printer_image_canvas=tk.Canvas(self, width=135, height=140)
        printer_image_canvas.pack()
        printer_image_canvas.create_image(135,140,image=printer_image,
                                          anchor='se')
        printer_image_canvas.image=printer_image

        #Start drawing ink bars
        ink_frame=tk.Frame(self)
        ink_frame.pack()
        for color, percent in data['supplies']:
            style, label = app.styles[color]
            inner_ink_frame=tk.Frame(ink_frame)
            inner_ink_frame.pack(pady=0)
            ink_bar=ttk.Progressbar(inner_ink_frame, value=percent or 0,
                                   style=style)
            ink_level=tk.Label(inner_ink_frame,
                               text='   '+('?' if percent is None
                                          else str(percent)+'%'),
                               foreground='red' if percent is not None
                                          and percent<=20 else 'black',
                               bd=0, width=5)
            ink_bar.pack(side=tk.LEFT, pady=0)
            ink_level.pack(side=tk.RIGHT, pady=0)
            balloon.bind(ink_bar, label)

        tray_frame=tk.Frame(self)
        tray_frame.pack(pady=(10,5))
        self.printer_deficit=0
        self.max_capacity=0
        for name, current, maximum, size in data['trays']:
            #Negative values mean the printer doesn't know the level
            known = maximum>0 and current>=0
            if known:
                tray_deficit=maximum-current
                percent_text=str(int((current/maximum)*100))+'%'
                self.max_capacity+=maximum
                self.printer_deficit+=tray_deficit
            else:
                tray_deficit=0
                percent_text='?'
            #Check if deficit is higher than 495 (at least one ream needed)
            #495 used because small trays at 10% are reported as 55/550
            label_color='red' if tray_deficit>=495 else 'black'

            tray_line=tk.Frame(tray_frame, height=20, width=230)

            #Set propogate to 0 so we can manually define frame size
            tray_line.pack_propagate(0)

            tray_info=tk.Frame(tray_line, height=20, width=115)
            tray_info.pack_propagate(0)
            tray_info.pack(side=tk.RIGHT)

            tray_size_label=tk.Label(tray_line, text=f"({size}):",
                                     font=(None, 9, 'bold'),
                                     foreground=label_color)
            tray_size_label.pack(side=tk.RIGHT, anchor=tk.E)

            tray_name_label=tk.Label(tray_line, text=name,
                                     font=(None, 9, 'bold'),
                                     foreground=label_color)
            tray_name_label.pack(side=tk.LEFT, anchor=tk.W)

            tray_percent=tk.Label(tray_info, text=percent_text,
                                  foreground=label_color,
                                  anchor=tk.E, width=5)
            tray_percent.pack(side=tk.LEFT)
            if known:
                tray_current_value=tk.Label(tray_info,
                                            text='('+str(current),
                                            foreground=label_color)
                tray_max_value=tk.Label(tray_info,
                                        text=str(maximum)+')',
                                        anchor=tk.E, foreground=label_color)
                slash=tk.Label(tray_info, text='/', bd=0,
                               foreground=label_color)
                tray_max_value.pack(side=tk.RIGHT)
                slash.pack(side=tk.RIGHT)
                tray_current_value.pack(side=tk.RIGHT)

            tray_line.pack(anchor=tk.W, fill=tk.BOTH, expand=True)

        #Start drawing the paper fill bar
        if self.max_capacity>0:
            self.paper_percentage=int(
                ((self.max_capacity-self.printer_deficit)/self.max_capacity)*100
                )
        else:
            self.paper_percentage=0
        bar_width=230
        paper_level_canvas=tk.Canvas(self, width=bar_width, height=20)
        paper_level_canvas.pack(side=tk.BOTTOM)
        paper_level_canvas.create_rectangle(2, 2, bar_width-1, 14,
                                            fill=default_bg,
                                            outline='red' if self.paper_percentage<=50 else '#757575')
        tray_fill_bar_width=int((self.paper_percentage*bar_width)/100)-1
        #Subtract 1 to avoid edge-to-edge overlap
        if tray_fill_bar_width < 3:
            tray_fill_bar_width=3
            #Prevent drawing in the negative direction when fill<=1%

        paper_level_canvas.create_rectangle(3, 3, tray_fill_bar_width, 14,
                                            fill='#cbcbcb',
                                            width=0
                                            )
        paper_level_canvas.create_text(115,8, font=(None, 8),
                                       text='Paper fill: ' +
                                       str(self.paper_percentage)+'%',
                                       fill='red' if self.paper_percentage<=50 else 'black'
                                       )
        balloon.bind(paper_level_canvas, '-' + str(self.printer_deficit)
                     + ' pages' + ' | -' +
                     str(round(self.printer_deficit/500, 2)) + ' reams')

    def draw_error(self, printer, err, app):
        log_name='RRM_log_' + app.current_time + '.txt'

        #Display error image
        tk.Label(self, text=printer['Name'], font=(None, 14)).pack()
        no_connection_canvas=tk.Canvas(self, width=155, height=150)
        no_connection_canvas.pack(pady=(100,0))
        no_connection_canvas.create_image(155, 150,
                                          image=app.no_connection,
                                          anchor=tk.SE)
        no_connection_canvas.image=app.no_connection
        no_connection_label_top=tk.Label(self, text='No Connection!',
                                      foreground='red', bd=0)
        no_connection_label_bottom=tk.Frame(self)
        no_connection_label2=tk.Label(no_connection_label_bottom,
                                      text='Check the ',
                                      foreground='red', bd=0)
        no_connection_label2.pack(side=tk.LEFT)
        no_connection_label3=tk.Label(no_connection_label_bottom,
                                     text='log file',
                                     foreground='blue', cursor='hand2',
                                     font=(None, 9, 'underline'),
                                     bd=0)
        no_connection_label3.bind('<Button-1>',
                        lambda event:open_file(log_name))
        no_connection_label3.pack(side=tk.LEFT)
        no_connection_label4=tk.Label(no_connection_label_bottom,
                                      text=' for more info',
                                      foreground='red', bd=0)
        no_connection_label4.pack(side=tk.RIGHT)
        no_connection_label_top.pack()
        no_connection_label_bottom.pack(anchor=tk.S)

        #log error and stack trace to logfile
        with open(log_name, 'a') as log:
            log.write('(' + datetime.now().strftime('%H:%M') + ') ' +
                      printer['Name'] + ': ' + str(err) + '\n' +
                      ''.join(traceback.format_exception(type(err), err,
                                                         err.__traceback__)) +
                      '\n')

    def deficit(self):
        #Return the printer's paper deficit
        return self.printer_deficit

if __name__ == '__main__':
    root=tk.Tk()
    #Set resizable to False in X,Y. Trust the program to resize itself :)
    root.resizable(False, False)
    MainApplication(root).pack(side='top', fill='both', expand=True)
    if 'TRAVIS' in os.environ:
        root.update()
        root.update_idletasks()
    else:
        root.mainloop()
