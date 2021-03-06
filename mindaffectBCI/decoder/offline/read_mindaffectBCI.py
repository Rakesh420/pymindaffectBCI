import os
import numpy as np
import re
from mindaffectBCI.utopiaclient import StimulusEvent, DataPacket, ModeChange

# named reg-exp to parse the different messages types log lines
serverts_re = re.compile(r'^sts:(?P<sts>[-0-9]*)\W')
clientip_re = re.compile(r'.*<-\W(?P<ip>[0-9.:]*)$')
stimevent_re = re.compile(r'^.*\Wts:(?P<ts>[-0-9]*)\W*v\[(?P<shape>[0-9x]*)\]:(?P<stimstate>.*) <-/.*$')
datapacket_re = re.compile(r'^.*\Wts:(?P<ts>[-0-9]*)\W*v\[(?P<shape>[0-9x]*)\]:(?P<samples>.*) <-/.*$')
modechange_re = re.compile(r'^.*\Wts:(?P<ts>[-0-9]*)\W.*mode:(?P<newmode>.*) <-/.*$')

def read_StimulusEvent(line:str):
    ''' read a stimulus event message from a text-file save version '''
    # named reg-ex to extract the bits we need
    res = stimevent_re.match(line)
    if res is None:
        return None
    ts = int(res['ts'])
    # parse sample into numpy array
    shape = np.fromstring(res['shape'].replace('x',','),sep=',',dtype=int)
    shape = shape[::-1] # N.B. python order, fastest last..
    stiminfo = res['stimstate'].replace('{','').replace('}',',')
    stiminfo = np.fromstring(stiminfo, sep=',', dtype=int)
    objIDs = stiminfo[0::2]
    stimstate = stiminfo[1::2]
    #print("SE ts:{} objIDs:{} state:{}".format(ts,objIDs,stimstate))
    return StimulusEvent(ts,objIDs,stimstate)
    
def read_DataPacket(line:str ):
    # named reg-ex to extract the bits we need
    res = datapacket_re.match(line)
    if res is None:
        return None
    ts = int(res['ts'])
    # parse sample into numpy array
    shape = np.fromstring(res['shape'].replace('x',','),sep=',',dtype=int)
    shape = shape[::-1] # N.B. python order, fastest last..
    samples = np.fromstring(res['samples'].replace(']','').replace('[',''),sep=',',dtype=np.float32)
    samples = samples.reshape(shape)
    return DataPacket(ts,samples)
    
def read_ModeChange(line:str):
    res = modechange_re.match(line)
    if res is None:
        return None
    ts = int(res['ts'])
    newmode = res['newmode']
    return ModeChange(ts,newmode)

def read_serverts(line:str):
    sts = serverts_re.match(line)
    sts = int(sts['sts']) if sts is not None else None
    return sts

def read_clientip(line:str):
    ip = clientip_re.match(line)
    ip = ip['ip'] if ip is not None else None
    return ip

def read_mindaffectBCI_message(line):
    if StimulusEvent.msgName in line:
        msg = read_StimulusEvent(line)
    elif DataPacket.msgName in line:
        msg = read_DataPacket(line)
    elif ModeChange.msgName in line:
        msg = read_ModeChange(line)
    else:
        msg = None
    # add the server time-stamp
    if msg :
        msg.sts = read_serverts(line) # server time-samp
        msg.clientip = read_clientip(line) # client ip-address
    return msg

def datapackets2array(msgs):
    data=[]
    last_ts = None
    for msg in msgs:
        samples = msg.samples
        ts   = msg.timestamp
        # add the time-stamp as the last channel
        # TODO []: interpolate the time-stamps for every sample
        if last_ts is not None:
            # interpolate the time-stamps, from last sample to last this packet inclusive
            tsch = np.linspace(last_ts, ts, samples.shape[0]+1, endpoint=True)
            tsch = tsch[1:,np.newaxis] # strip the last valid time-stamp
        else:
            # if in doubt, give all the same time-stamp...
            tsch = np.ones((samples.shape[0],1))*ts
        samples = np.append(samples,tsch,-1)
        data.append(samples)
        last_ts = ts
    # convert data into single np array
    data = np. concatenate(data,0)
    return data    

def rewrite_timestamps2servertimestamps(msgs):
    ''' rewrite message time-stamps  to best-fit server time-stamps '''
    # get the client-timestamp, server-timestamp pairs
    x = np.array([msg.timestamp for msg in msgs]) #  from: client timestamp
    y = np.array([msg.sts for msg in msgs]) # to: server timestamp
    # Warning: strip and -1....
    invalidts = np.logical_or(x==-1,y==-1)
    x = x[~invalidts]
    y = y[~invalidts]
    # add constant feature  for the intercept
    x = np.append(x[:,np.newaxis],np.ones((x.shape[0],1)),1)
    # LS  solve
    # TODO[X]: use a robust least squares which allows for outliers due to network delays
    # TODO[]: use a proper weighted least squares robust estimator, also for on-line
    y_fit = y.copy()
    for i in range(3):
        ab,res,_,_ = np.linalg.lstsq(x,y_fit,rcond=-1)
        y_est = x[:,0]*ab[0] + ab[1]
        err = y - y_est # server > true, clip positive errors
        scale = np.mean(np.abs(err))
        clipIdx = err > 3*scale
        #print("{} overestimates".format(np.sum(clipIdx)))
        y_fit[clipIdx] = y_est[clipIdx] + 3*scale
        clipIdx = err < -3*scale
        #print("{} underestimates".format(np.sum(clipIdx)))
        y_fit[clipIdx] = y_est[clipIdx] - 3*scale
    #print("ab={}".format(ab))
    # now rewrite the client time-stamps
    for m in msgs:
        m.rawtimestamp = m.timestamp
        m.timestamp = m.rawtimestamp*ab[0] + ab[1]
    return (msgs,ab)

    
def read_mindaffectBCI_messages( fn:str ):
    ''' read the data from a text-file save version into a list of messages,
        WARNING: this reads the  messages as raw, and does *not* try to time-stamp clocks
                 w.r.t.  the message source.  To compare messages between clients you will
                 need to do this manually! '''
    fn = os.path.expanduser(fn)
    with open(fn,'r') as file:
        msgs=[]
        for line in file:
            msg = read_mindaffectBCI_message(line)
            if msg is not None:
                msgs.append(msg)

    # TODO [X]: intelligent time-stamp re-writer taking account of the client-ip
    clientips = [ m.clientip for m in msgs ]
    for client in set(clientips):
        clientmsgs = [ c for c in msgs if c.clientip == client ]
        _, ab = rewrite_timestamps2servertimestamps(clientmsgs)
    return msgs

def read_mindaffectBCI_data_messages( fn:str ):
    ''' read the data from a text-file save version into a dataarray and message list '''
    rawmsgs = read_mindaffectBCI_messages(fn)
    # split into datapacket messages and others
    data=[]
    msgs=[]
    for m in rawmsgs:
        if isinstance(m,DataPacket):
            data.append(m)
        else:
            msgs.append(m)

    # rewrite the timestamps to the common server clock
    #data, data2server_ab = rewrite_timestamps2servertimestamps(data)
    #msgs, msgs2server_ab = rewrite_timestamps2servertimestamps(msgs)

    # convert the data messages into a single numpy array,
    # with (interpolated) time-stamps in the final 'channel'
    data = datapackets2array(data)
    
    return (data,msgs)


def testcase(fn=None):
    ''' testcase, load reference datafile '''
    if fn is None:
        fn = '../../resources/example_data/mindaffectBCI.txt'

    print("read messages")
    msgs = read_mindaffectBCI_messages(fn)
    for msg in msgs[:100]:
        print("{}".format(msg))

    print("read data messages")
    data,msgs = read_mindaffectBCI_data_messages(fn)
    print("Data({})={}".format(data.shape,data))
    for m in msgs[:100]:
        print("{}".format(m))

    
if __name__=="__main__":
    import sys
    fn = None
    #if len(sys.argv) > 0:
    #    fn = sys.argv[1]
    testcase(fn)
