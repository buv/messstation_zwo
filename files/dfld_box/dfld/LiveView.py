import os
import time
import codecs
import socket
import logging
from .util import calc_crc

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
            else:
                logging.warning('LiveView not active: DFLD_LIVEVIEW must be in format host:port')
        else:
            logging.info('LiveView not active: missing environment variables DFLD_LIVEVIEW, DFLD_REGION, DFLD_STATION, DFLD_CKSUM')


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

