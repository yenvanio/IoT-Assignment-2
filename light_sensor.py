#! /usr/bin/env python3
import struct
import enum
import serial
import time
from bluepy.btle import UUID, Peripheral, DefaultDelegate, AssignedNumbers
import struct
import math
import time
import sys
import argparse

def _TI_UUID(val):
    return UUID("%08X-0451-4000-b000-000000000000" % (0xF0000000+val))

# Sensortag versions
AUTODETECT = "-"
SENSORTAG_V1 = "v1"
SENSORTAG_2650 = "CC2650"

class SensorBase:
    # Derived classes should set: svcUUID, ctrlUUID, dataUUID
    sensorOn  = struct.pack("B", 0x01)
    sensorOff = struct.pack("B", 0x00)

    def __init__(self, periph):
        self.periph = periph
        self.service = None
        self.ctrl = None
        self.data = None

    def enable(self):
        if self.service is None:
            self.service = self.periph.getServiceByUUID(self.svcUUID)
        if self.ctrl is None:
            self.ctrl = self.service.getCharacteristics(self.ctrlUUID) [0]
        if self.data is None:
            self.data = self.service.getCharacteristics(self.dataUUID) [0]
        if self.sensorOn is not None:
            self.ctrl.write(self.sensorOn,withResponse=True)

    def read(self):
        return self.data.read()

    def disable(self):
        if self.ctrl is not None:
            self.ctrl.write(self.sensorOff)

def calcPoly(coeffs, x):
    return coeffs[0] + (coeffs[1]*x) + (coeffs[2]*x*x)

class OpticalSensorOPT3001(SensorBase):
    svcUUID  = _TI_UUID(0xAA70)
    dataUUID = _TI_UUID(0xAA71)
    ctrlUUID = _TI_UUID(0xAA72)

    def __init__(self, periph):
       SensorBase.__init__(self, periph)

    def read(self):
        '''Returns value in lux'''
        raw = struct.unpack('<h', self.data.read()) [0]
        m = raw & 0xFFF;
        e = (raw & 0xF000) >> 12;
        return 0.01 * (m << e)

class SensorTag(Peripheral):
    def __init__(self,addr,version=AUTODETECT):
        Peripheral.__init__(self,addr)
        if version==AUTODETECT:
            svcs = self.discoverServices()
            if _TI_UUID(0xAA70) in svcs:
                version = SENSORTAG_2650
            else:
                version = SENSORTAG_V1

        fwVers = self.getCharacteristics(uuid=AssignedNumbers.firmwareRevisionString)
        if len(fwVers) >= 1:
            self.firmwareVersion = fwVers[0].read().decode("utf-8")
        else:
            self.firmwareVersion = u''

        if version==SENSORTAG_V1:
            self.lightmeter = None
        elif version==SENSORTAG_2650:
            self.lightmeter = OpticalSensorOPT3001(self)

class MessageType(enum.Enum):
    Text = 0
    Numeric = 1
    Logic = 2

def decodeMessage(s, msgType):
    payloadSize = struct.unpack_from('<H', s, 0)[0]
    remnant = None
    if payloadSize < 5:       # includes the mailSize
        raise BufferError('Payload size is too small')

    a,b,c,d = struct.unpack_from('<4B', s, 2)
    if a != 1 or b != 0 or c != 0x81 or d != 0x9e:
        raise BufferError('Header is not correct.  Expecting 01 00 81 9e')

    mailSize = struct.unpack_from('<B', s, 6)[0]

    if payloadSize < (5 + mailSize):  # includes the valueSize
        raise BufferError('Payload size is too small')

    mailBytes = struct.unpack_from('<' + str(mailSize) + 's', s, 7)[0]
    mail = mailBytes.decode('ascii')[:-1]

    valueSize = struct.unpack_from('<H', s, 7 + mailSize)[0]
    if payloadSize < (7 + mailSize + valueSize):  # includes the valueSize
        raise BufferError('Payload size does not match the packet')

    if msgType == MessageType.Logic:
        if valueSize != 1:
            raise BufferError('Value size is not one byte required for Logic Type')
        valueBytes = struct.unpack_from('<B', s, 9 + mailSize)[0]
        value = True if valueBytes != 0 else False
    elif msgType == MessageType.Numeric:
        if valueSize != 4:
            raise BufferError('Value size is not four bytes required for Numeric Type')
        value = struct.unpack_from('<f', s, 9 + mailSize)[0]
    else:
        valueBytes = struct.unpack_from('<' + str(valueSize) + 's', s, 9 + mailSize)[0]
        value = valueBytes.decode('ascii')[:-1]
        if len(s) > (payloadSize + 2):
            remnant = None
        remnant = s[(payloadSize) + 2:]

    return (mail, value, remnant)

def encodeMessage(msgType, mail, value):
    mail = mail + '\x00'
    mailBytes = mail.encode('ascii')
    mailSize = len(mailBytes)
    fmt = '<H4BB' + str(mailSize) + 'sH'

    if msgType == MessageType.Logic:
        valueSize = 1
        valueBytes = 1 if value is True else 0
        fmt += 'B'
    elif msgType == MessageType.Numeric:
        valueSize = 4
        valueBytes = float(value)
        fmt += 'f'
    else:
        value = value + '\x00'
        valueBytes = value.encode('ascii')
        valueSize = len(valueBytes)
        fmt += str(valueSize) + 's'

    payloadSize = 7 + mailSize + valueSize
    s = struct.pack(fmt, payloadSize, 0x01, 0x00, 0x81, 0x9e, mailSize, mailBytes, valueSize, valueBytes)
    return s

if __name__ == "__main__":
    #Setup Serial Port
    EV3 = serial.Serial('/dev/rfcomm0')

    parser = argparse.ArgumentParser()
    parser.add_argument('host', action='store',help='MAC of BT device')
    parser.add_argument('-L','--light', action='store_true', default=False)

    arg = parser.parse_args(sys.argv[1:])

    print('Connecting to ' + arg.host)
    tag = SensorTag(arg.host)

    while(tag.isConnected()):
        if (arg.light or arg.all) and tag.lightmeter is not None:
            tag.lightmeter.enable()

        if (arg.light or arg.all) and tag.lightmeter is not None:
               print("Light: ", tag.lightmeter.read())

        s = encodeMessage(MessageType.Numeric, 'Light', tag.lightmeter.read())
        print('Sending the following message\n')
        EV3.write(s)
        time.sleep(1)
    error_message = encodeMessage(MessageType.Text, 'Error', 'Stop Operation of Vehicle.')
    del tag
    EV3.close()
