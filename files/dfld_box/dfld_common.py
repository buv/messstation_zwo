import os
import time
import codecs
import socket
import logging

class LiveView:
    """
    Class to send live data to the DFLD server.
    """

    def __init__(self):
        """
        Initialize the LiveView class.
        :param server: server address
        :param port: server port
        """

        self.active = False
        self.server = None
        self.port = None
        self.socket = None
        self.data = None
        self.cksum = None
        self.next_attempt = None

        required_env = ['DFLD_LIVEVIEW', 'DFLD_REGION', 'DFLD_STATION', 'DFLD_CKSUM']
        if all(k in os.environ for k in required_env):
            server_param = os.environ['DFLD_LIVEVIEW'].split(':')
            if len(server_param) == 2:
                self.server = server_param[0]
                self.port = int(server_param[1])
                self.init_socket()
                self.cksum = int(os.environ['DFLD_CKSUM'])

                # convert the data to bytes from 2-byte integers
                self.data = bytearray()
                self.data.extend(int(1).to_bytes(2, byteorder='big')) # version
                self.data.extend(int(0).to_bytes(2, byteorder='big')) # customer
                self.data.extend(int(os.environ['DFLD_REGION']).to_bytes(2, byteorder='big')) # region
                self.data.extend(int(os.environ['DFLD_STATION']).to_bytes(2, byteorder='big')) # station
                # append timestamp of now to the data
                self.data.extend(int(time.time()).to_bytes(4, byteorder='big'))
                self.data.extend(b'AS')
                # append dBA value * 128
                self.data.extend(int(0*128).to_bytes(2, byteorder='big'))
                self.data.extend(int(0).to_bytes(2, byteorder='big')) # status
                # append CRC value
                crc = calc_crc(self.data, self.cksum)
                self.data.extend(crc.to_bytes(2, byteorder='big'))

                self.active = True
                logging.info(f'LiveView active: {self.server}:{self.port}')


    def set_next_attempt(self):
        """
        Set the next attempt to initialize the socket.
        """
        self.next_attempt = time.time() + 3600


    def init_socket(self):
        """
        Initialize the socket.
        """
        if self.next_attempt and time.time() < self.next_attempt:
            return

        if self.active:
            try:
                self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.socket.settimeout(1)
                logging.debug('socket initialized')
                self.next_attempt = None
            except socket.error as e:
                logging.error(f'Error creating socket: {e}')
                self.socket = None
                # set next attempt to 1 hour
                self.set_next_attempt()
        else:
            self.socket = None


    def send(self, value):
        """
        Send data to the DFLD server.
        :param data: data to send
        """
        # check if socket is initialized
        if self.socket is None:
            self.init_socket()

        if self.active and self.socket:
            # write timestamp to data
            self.data[8:12] = int(time.time()).to_bytes(4, byteorder='big')
            # write value to data
            self.data[14:16] = int(value * 128).to_bytes(2, byteorder='big')
            # write CRC to data
            crc = calc_crc(self.data[0:18], self.cksum)
            self.data[18:20] = crc.to_bytes(2, byteorder='big')

            # send the data to the server
            try:
                self.socket.sendto(self.data, (self.server, self.port))
            except socket.error as e:
                logging.error(f'Error sending data: {e}')
                self.socket = None
                self.set_next_attempt()
            logging.debug(f'LiveView data sent: {self.data.hex()}')


# encode a string from clear text to obfuscated text
def obfuscate_string(string):
    """
    obfuscate a string
    :param string: string to obfuscate
    :return: obfuscated string
    """
    # encode the string to bytes using utf-8 encoding
    byte_string = codecs.encode(string, 'rot13').encode('utf-8')
    # encode the bytes to base64
    obfuscated_string = codecs.encode(byte_string, 'base64').decode('utf-8')
    return obfuscated_string

# decode a string from obfuscated text to clear text
def deobfuscate_string(obfuscated_string):
    """
    deobfuscate a string
    :param obfuscated_string: obfuscated string
    :return: clear text string
    """
    # decode the obfuscated string from base64 to bytes
    byte_string = codecs.decode(obfuscated_string.encode('utf-8'), 'base64')
    # decode the bytes to string using utf-8 encoding
    clear_text_string = codecs.decode(byte_string.decode('utf-8'), 'rot13')
    return clear_text_string

# This function contains the CRC calculation function for the DFLD protocol.
def calc_crc(data, init = 0):
    CrcTab = [
        0x0000, 0x1021, 0x2042, 0x3063, 0x4084, 0x50a5, 0x60c6, 0x70e7,
        0x8108, 0x9129, 0xa14a, 0xb16b, 0xc18c, 0xd1ad, 0xe1ce, 0xf1ef,
        0x1231, 0x0210, 0x3273, 0x2252, 0x52b5, 0x4294, 0x72f7, 0x62d6,
        0x9339, 0x8318, 0xb37b, 0xa35a, 0xd3bd, 0xc39c, 0xf3ff, 0xe3de,
        0x2462, 0x3443, 0x0420, 0x1401, 0x64e6, 0x74c7, 0x44a4, 0x5485,
        0xa56a, 0xb54b, 0x8528, 0x9509, 0xe5ee, 0xf5cf, 0xc5ac, 0xd58d,
        0x3653, 0x2672, 0x1611, 0x0630, 0x76d7, 0x66f6, 0x5695, 0x46b4,
        0xb75b, 0xa77a, 0x9719, 0x8738, 0xf7df, 0xe7fe, 0xd79d, 0xc7bc,
        0x48c4, 0x58e5, 0x6886, 0x78a7, 0x0840, 0x1861, 0x2802, 0x3823,
        0xc9cc, 0xd9ed, 0xe98e, 0xf9af, 0x8948, 0x9969, 0xa90a, 0xb92b,
        0x5af5, 0x4ad4, 0x7ab7, 0x6a96, 0x1a71, 0x0a50, 0x3a33, 0x2a12,
        0xdbfd, 0xcbdc, 0xfbbf, 0xeb9e, 0x9b79, 0x8b58, 0xbb3b, 0xab1a,
        0x6ca6, 0x7c87, 0x4ce4, 0x5cc5, 0x2c22, 0x3c03, 0x0c60, 0x1c41,
        0xedae, 0xfd8f, 0xcdec, 0xddcd, 0xad2a, 0xbd0b, 0x8d68, 0x9d49,
        0x7e97, 0x6eb6, 0x5ed5, 0x4ef4, 0x3e13, 0x2e32, 0x1e51, 0x0e70,
        0xff9f, 0xefbe, 0xdfdd, 0xcffc, 0xbf1b, 0xaf3a, 0x9f59, 0x8f78,
        0x9188, 0x81a9, 0xb1ca, 0xa1eb, 0xd10c, 0xc12d, 0xf14e, 0xe16f,
        0x1080, 0x00a1, 0x30c2, 0x20e3, 0x5004, 0x4025, 0x7046, 0x6067,
        0x83b9, 0x9398, 0xa3fb, 0xb3da, 0xc33d, 0xd31c, 0xe37f, 0xf35e,
        0x02b1, 0x1290, 0x22f3, 0x32d2, 0x4235, 0x5214, 0x6277, 0x7256,
        0xb5ea, 0xa5cb, 0x95a8, 0x8589, 0xf56e, 0xe54f, 0xd52c, 0xc50d,
        0x34e2, 0x24c3, 0x14a0, 0x0481, 0x7466, 0x6447, 0x5424, 0x4405,
        0xa7db, 0xb7fa, 0x8799, 0x97b8, 0xe75f, 0xf77e, 0xc71d, 0xd73c,
        0x26d3, 0x36f2, 0x0691, 0x16b0, 0x6657, 0x7676, 0x4615, 0x5634,
        0xd94c, 0xc96d, 0xf90e, 0xe92f, 0x99c8, 0x89e9, 0xb98a, 0xa9ab,
        0x5844, 0x4865, 0x7806, 0x6827, 0x18c0, 0x08e1, 0x3882, 0x28a3,
        0xcb7d, 0xdb5c, 0xeb3f, 0xfb1e, 0x8bf9, 0x9bd8, 0xabbb, 0xbb9a,
        0x4a75, 0x5a54, 0x6a37, 0x7a16, 0x0af1, 0x1ad0, 0x2ab3, 0x3a92,
        0xfd2e, 0xed0f, 0xdd6c, 0xcd4d, 0xbdaa, 0xad8b, 0x9de8, 0x8dc9,
        0x7c26, 0x6c07, 0x5c64, 0x4c45, 0x3ca2, 0x2c83, 0x1ce0, 0x0cc1,
        0xef1f, 0xff3e, 0xcf5d, 0xdf7c, 0xaf9b, 0xbfba, 0x8fd9, 0x9ff8,
        0x6e17, 0x7e36, 0x4e55, 0x5e74, 0x2e93, 0x3eb2, 0x0ed1, 0x1ef0
    ]
    crc = init
    for byte in data:
        crc = CrcTab[((crc >> 8) & 255)] ^ (crc << 8) ^ byte
    return crc & 0xffff

