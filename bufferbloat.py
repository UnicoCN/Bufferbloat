from mininet.topo import Topo
from mininet.node import CPULimitedHost
from mininet.link import TCLink
from mininet.net import Mininet
from mininet.log import lg, info
from mininet.util import dumpNodeConnections
from mininet.cli import CLI

from mininet.node import OVSController

from subprocess import Popen, PIPE
from time import sleep, time
from multiprocessing import Process
from argparse import ArgumentParser

from monitor import monitor_qlen

import numpy as np
import sys
import os
import math

parser = ArgumentParser(description="Bufferbloat tests")
parser.add_argument('--bw-host', '-B',
                    type=float,
                    help="Bandwidth of host links (Mb/s)",
                    default=1000)

parser.add_argument('--bw-net', '-b',
                    type=float,
                    help="Bandwidth of bottleneck (network) link (Mb/s)",
                    required=True)

parser.add_argument('--delay',
                    type=float,
                    help="Link propagation delay (ms)",
                    required=True)

parser.add_argument('--dir', '-d',
                    help="Directory to store outputs",
                    required=True)

parser.add_argument('--time', '-t',
                    help="Duration (sec) to run the experiment",
                    type=int,
                    default=10)

parser.add_argument('--maxq',
                    type=int,
                    help="Max buffer size of network interface in packets",
                    default=100)

parser.add_argument('--http3',
                    help="Run the experiment using http3",
                    action='store_true',
                    default=False)

# Linux uses CUBIC-TCP by default that doesn't have the usual sawtooth
# behaviour.  For those who are curious, invoke this script with
# --cong cubic and see what happens...
# sysctl -a | grep cong should list some interesting parameters.
parser.add_argument('--cong',
                    help="Congestion control algorithm to use",
                    default="reno")

# Expt parameters
args = parser.parse_args()

class BBTopo(Topo):
    "Simple topology for bufferbloat experiment."

    def build(self):
        # TODO: create two hosts
        h1 = self.addHost( 'h1' )
        h2 = self.addHost( 'h2' )
        router = self.addSwitch( 's0', protocols='OpenFlow13')
        # Here I have created a switch.  If you change its name, its
        # interface names will change from s0-eth1 to newname-eth1.
        # router = self.addSwitch('s0', protocals='OpenFlow13')

        bw_host = args.bw_host
        bw_net = args.bw_net
        delay = args.delay
        maxq = args.maxq

        # TODO: Add links with appropriate characteristics
        
        self.addLink( h1, router, bw=bw_host, delay='%sms' %delay, max_queue_size=maxq )
        self.addLink( h2, router, bw=bw_net, delay='%sms'%delay, max_queue_size=maxq )
        
        # # Add links
        # self.addLink(h1, switch, bw=bw_host, delay='%sms' % delay, max_queue_size=maxq)
        # self.addLink(h2, switch, bw=bw_net, delay='%sms' % delay, max_queue_size=maxq)
        # self.addLink(h1, h2, bw=bw_host, delay='%sms' % delay, max_queue_size=maxq)

# Simple wrappers around monitoring utilities.  You are welcome to
# contribute neatly written (using classes) monitoring scripts for
# Mininet!

def start_iperf(net):
    h2 = net.get('h2')
    print("Starting iperf server...")
    # For those who are curious about the -w 16m parameter, it ensures
    # that the TCP flow is not receiver window limited.  If it is,
    # there is a chance that the router buffer may not get filled up.
    server = h2.popen("iperf -s -w 16m")
    # TODO: Start the iperf client on h1.  Ensure that you create a
    # long lived TCP flow.
    h1 = net.get('h1')
    h1.cmd("iperf -c %s -t %s" % (h2.IP(), args.time))

def start_qmon(iface, interval_sec=0.1, outfile="q.txt"):
    monitor = Process(target=monitor_qlen,
                      args=(iface, interval_sec, outfile))
    monitor.start()
    return monitor

def start_ping(net):
    # TODO: Start a ping train from h1 to h2 (or h2 to h1, does it
    # matter?)  Measure RTTs every 0.1 second.  Read the ping man page
    # to see how to do this.

    # Hint: Use host.popen(cmd, shell=True).  If you pass shell=True
    # to popen, you can redirect cmd's output using shell syntax.
    # i.e. ping ... > /path/to/ping.
    h1 = net.get('h1')
    h2 = net.get('h2')
    popen = h1.popen("ping -c %s -i 0.1 %s > %s/ping.txt" % (args.time * 10, h2.IP(), args.dir), shell = True)
    popen.communicate()
    

def start_webserver(net):
    h1 = net.get('h1')
    proc = h1.popen("python3 webserver.py", shell=True)
    sleep(1)
    return [proc]

def get_timings(net, h1, h2):
    timings = []
    fetch = "curl -o /dev/null -s -w %{time_total} " + h1.IP() + "/http/index.html"
    time = h2.popen(fetch).communicate()[0]
    timings.append(float(time))
    numpy_timings = np.array(timings)
    return np.mean(numpy_timings)

def bufferbloat():
    if args.http3:
        print("http3")
    else:
        print("tcp")
    if not os.path.exists(args.dir):
        os.makedirs(args.dir)
    os.system("sysctl -w net.ipv4.tcp_congestion_control=%s" % args.cong)
    topo = BBTopo()
    net = Mininet(topo=topo, host=CPULimitedHost, link=TCLink, controller=OVSController)
    net.start()
    # This dumps the topology and how nodes are interconnected through
    # links.
    dumpNodeConnections(net.hosts)
    # This performs a basic all pairs ping test.
    h1, h2, router = net.get( 'h1', 'h2', 's0' )
    print(h1.IP(), ' ', h2.IP(), ' ', router.IP())
    
    net.pingAll()
    # TODO: Start monitoring the queue sizes.  Since the switch I
    # created is "s0", I monitor one of the interfaces.  Which
    # interface?  The interface numbering starts with 1 and increases.
    # Depending on the order you add links to your network, this
    # number may be 1 or 2.  Ensure you use the correct number.
    qmon = start_qmon(iface='s0-eth2',
                      outfile='%s/q.txt' % (args.dir))

    # TODO: Start iperf, webservers, etc.
    # start_iperf(net)
    iperf_proc = Process(target=start_iperf, args=(net,))
    ping_proc = Process(target=start_ping, args=(net,))
    iperf_proc.start()
    ping_proc.start()
    start_webserver(net)

    # TODO: measure the time it takes to complete webpage transfer
    # from h1 to h2 (say) 3 times.  Hint: check what the following
    # command does: curl -o /dev/null -s -w %{time_total} google.com
    # Now use the curl command to fetch webpage from the webserver you
    # spawned on host h1 (not from google!)

    # As a sanity check, before the time measurement, check whether the
    # webpage is transferred successfully by checking the response from curl

    # Hint: have a separate function to do this and you may find the
    # loop below useful.
    start_time = time()
    measurements = []
    download_times = []
    h1 = net.get("h1")
    h2 = net.get("h2")
    while True:
        # do the measurement (say) 3 times.
        measurements.append(get_timings(net, h1, h2))

        sleep(5)
        now = time()
        delta = now - start_time
        if delta > args.time:
            break
        download_times.append(delta) #record times to use in download graph 
        print("%.1fs left..." % (args.time - delta))

    # TODO: compute average (and standard deviation) of the fetch
    # times.  You don't need to plot them.  Just note it in your
    # README and explain.

    print("Writing results...")
    max_q = args.maxq
    f = open("./" + str(args.cong) + "Results-" + str(max_q) +".txt", "w+")
    numpy_measurements = np.array(measurements)
    f.write("average: %s \n" % np.mean(numpy_measurements))
    f.write("std dev: %s \n" % np.std(numpy_measurements))  
    f.close()

    # Hint: The command below invokes a CLI which you can use to
    # debug.  It allows you to run arbitrary commands inside your
    # emulated hosts h1 and h2.
    # CLI(net)

    qmon.terminate()
    net.stop()
    # Ensure that all processes you create within Mininet are killed.
    # Sometimes they require manual killing.
    Popen("pgrep -f webserver.py | xargs kill -9", shell=True).wait()

if __name__ == "__main__":
    bufferbloat()
