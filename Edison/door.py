# door.py
# IoT Video Series Airlock Door Controller
# Shawn Hymel @ SparkFun Electronics
# May 20, 2015
# https://github.com/sparkfun/IoT_Airlock
#
# Runs on an Edison to receive input from an Arduino, which controls the
# security sensors. The Edison posts photos of uncleared individuals to Twitter
# and opens doors for cleared individuals.
#
# Seriously, this code is sketchy and finicky. Use at your own discretion. The
# Edison has a habit of resetting the webcam, and there is a known bug where
# SSH will slow to a crawl after running this code once. Annoying, I know. Feel
# free to use any of the code you want (most of it seems to work without a
# problem in separate pieces). I don't blame you if you don't use it to actually
# control locks on your door. I wouldn't. It was made to be a silly demo.
#
# This code is beerware; if you see me (or any other SparkFun 
# employee) at the local, and you've found our code helpful, please
# buy us a round!
# 
# To install bluepy:
# http://shawnhymel.com/665/using-python-and-ble-to-receive-data-from-the-rfduino/
# Copy the bluepy folder to IoT_Airlock/Edison. You can also just call 'make' in
# the bluepy folder, which builds bluepy in this project.
#
# Distributed as-is; no warranty is given.

################################################################################
# Global Constants
################################################################################

from twython import Twython, TwythonStreamer, TwythonError
from bluepy.btle import UUID, Peripheral
import pygame.camera
import pygame.image
import threading
import signal
import sys
import mraa
import time
import struct

# Parameters
DEBUG = 1
STOP_STREAM_ON_FAIL = True
IMG_NAME = 'intruder.jpeg'
ONLINE_MSG = 'Good morning! I am awake and ready to protect the door.'
SUCCESS_MSG = 'Welcome home, '
FAILURE_MSG = 'Someone is at the door.'
NAMES = ['@Sarah_Al_Mutlaq', '@ShawnHymel', '@NorthAllenPoole']
HANDLE = '@SFE_Fellowship'
INNER_ADDR = 'F9:D8:C2:B9:77:E9'
OUTER_ADDR = 'D4:2C:92:60:C2:D5'
LOCK_DELAY = 1 # Seconds

# GPIO pins
SUCCESS_PIN = 31    # GP44
FAILURE_PIN = 45    # GP45
STATUS_PIN_1 = 32   # GP46
STATUS_PIN_0 = 46   # GP47
REED_OUTER_PIN = 33 # GP48
DOORBELL_PIN = 47   # GP49
REED_INNER_PIN = 14 # GP13
DENY_PIN = 36       # GP14
APPROVE_PIN = 48    # GP15

# Command set from Tweets
TWEET_CLEAR = 0
TWEET_LET_IN = 1
TWEET_LET_OUT = 2

# Bluetooth message constants
MSG_LOCK = 0x10
MSG_UNLOCK = 0x11
MSG_STATE_REQ = 0x12

# Define read and write UUIDs
READ_UUID = UUID(0x2221)
WRITE_UUID = UUID(0x2222)

# Twitter credentials
APP_KEY = 'xxxxxxxxxxxxx'
APP_SECRET = 'xxxxxxxxxxxxx'
OAUTH_TOKEN = 'xxxxxxxxxxxxx'
OAUTH_TOKEN_SECRET = 'xxxxxxxxxxxxx'

################################################################################
# Global Variables
################################################################################

g_mainloop = False
g_streamer = None
g_command = 0

################################################################################
# Classes
################################################################################

# Streamer class. Use this to look for commands on Twitter.
class ListenStreamer(TwythonStreamer):

    # [Constructor] Inherits a Twython streamer
    #def __init__(self, parent, app_key, app_secret, oauth_token, 
    #                oauth_token_secret, timeout=300, retry_count=None, 
    #                retry_in=10, client_args=None, handlers=None, 
    #                chunk_size=1):
    #    TwythonStreamer.__init__(self, app_key, app_secret, oauth_token, 
    #                oauth_token_secret, timeout=300, retry_count=None, 
    #                retry_in=10, client_args=None, handlers=None, 
    #                chunk_size=1)
    #    self.parent = parent

    # Callback from streamer when tweet matches the search term(s)
    def on_success(self, data):

        global g_command

        # See if we have a valid Tweet with text
        if 'text' in data:
            msg = data['text'].encode('utf-8')
            if DEBUG > 0:
                print msg
            
            # Verify that author is one of the approved members
            if any(('@' + data['user']['screen_name']) in u for u in NAMES):
            
                # Look for a keyword in the Tweet
                for word in msg.split():
                    if word == 'in':
                        g_command = TWEET_LET_IN
                        break
                    elif word == 'out':
                        g_command = TWEET_LET_OUT
                        break
                
    # Callback from streamer if error occurs
    def on_error(self, status_code, data):
        print status_code, data
        if STOP_STREAM_ON_FAIL:
            self.stop()
        
    # Called from main thread to stop the streamer
    def stop(self):
        self.disconnect()

# TweetFeed class sets up the streamer and provides access to the tweets
class TweetFeed:

    # [Constructor] Set up streamer thread
    def __init__(self, twitter_auth):
    
        # Extract authentication tokens
        self.app_key = twitter_auth['app_key']
        self.app_secret = twitter_auth['app_secret']
        self.oauth_token = twitter_auth['oauth_token']
        self.oauth_token_secret = twitter_auth['oauth_token_secret']
                            
        # Setup Twitter object to send tweets
        self.twitter = Twython( self.app_key, 
                                self.app_secret, 
                                self.oauth_token, 
                                self.oauth_token_secret)

        # Create a variable that contains the type of message we received
        self.command = 0
        
    # [Public] Stop streamer
    def stopStreamer(self, timeout=None):
        self.track_stream.stop()
        self.listen_thread.join(timeout)
        del self.listen_thread

    # [Public] Send a Tweet
    def tweet(self, msg):

        # Construct the message
        time_now = time.strftime('%m/%d/%Y (%H:%M:%S)')
        msg = time_now + ' ' + msg
        if DEBUG > 0:
            print msg

        # Post to Twitter
        try:
            self.twitter.update_status(status=msg)
        except TwythonError as e:
            print e
            
    # [Public] Tweet a photo
    def tweetPhoto(self, cam, msg):

        # Take a photo
        cam.start()
        img = cam.get_image()
        pygame.image.save(img, IMG_NAME)
        cam.stop()

        # Wait for file to finish writing
        time.sleep(1)
        
        # Construct the message
        time_now = time.strftime('%m/%d/%Y (%H:%M:%S)')
        msg = time_now + ' ' + msg
        for n in NAMES:
            msg = msg + ' ' + n
        if DEBUG > 0:
            print msg

        # Post to Twitter
        try:
            fp = open(IMG_NAME, 'rb')
            image_id = self.twitter.upload_media(media=fp)
            self.twitter.update_status(media_ids=[image_id['media_id']], \
                                                                status=msg)
        except TwythonError as e:
            print e
        finally:
            fp.close()

    # [Public] Read the command flag
    def getCommand(self):
        return self.command

    # [Public] Set the command flag
    def setCommand(self, c):
        self.command = c
 
################################################################################
# Functions
################################################################################

# Handle ctrl-C events
def signalHandler(signal, frame):
    global g_mainloop
    if DEBUG > 0:
        print 'Ctrl-C pressed. Exiting nicely.'
    g_mainloop = False

# Global Listener creation (test because the object one doesn't work)
def createStreamer():
    global g_streamer
    time.sleep(5)
    g_streamer = ListenStreamer(APP_KEY, \
                                APP_SECRET, \
                                OAUTH_TOKEN, \
                                OAUTH_TOKEN_SECRET)
    g_streamer.statuses.filter(track=HANDLE)
    
# Let someone in or out of the airlock
def openDoor(p, w_ch, reed):

    global g_mainloop

    # Unlock the door
    if DEBUG > 0:
        print 'Unlocking door.'
    bleSend(p, w_ch, MSG_UNLOCK)

    # Wait for that door to be opened and then closed
    if DEBUG > 0:
        print 'Waiting for the door to be opened...'
    while reed.read() == 0 and g_mainloop:
        pass
    time.sleep(LOCK_DELAY)
    if DEBUG > 0:
        print 'Waiting for the door to be closed...'
    while reed.read() == 1 and g_mainloop:
        pass
    time.sleep(LOCK_DELAY)
    if DEBUG > 0:
        print 'Door closed. Locking.'
    bleSend(p, w_ch, MSG_LOCK)
    
# Send a message to a Lockitron
def bleSend(p, w_ch, msg):
    msg = struct.pack('i', msg)
    w_ch.write(msg)
  
################################################################################
# Main
################################################################################

def main():

    global g_mainloop
    global g_streamer
    global g_command

    # Reset camera to prevent black/pink images on first run through
    pygame.camera.init()
    cam = pygame.camera.Camera(pygame.camera.list_cameras()[0])
    cam.start()
    img = cam.get_image()
    pygame.image.save(img, IMG_NAME)
    cam.stop()
    pygame.camera.quit()
    img = None

    # Initialize GPIO
    in_success = mraa.Gpio(SUCCESS_PIN)
    in_failure = mraa.Gpio(FAILURE_PIN)
    in_status_0 = mraa.Gpio(STATUS_PIN_0)
    in_status_1 = mraa.Gpio(STATUS_PIN_1)
    doorbell = mraa.Gpio(DOORBELL_PIN)
    reed_outer = mraa.Gpio(REED_OUTER_PIN)
    reed_inner = mraa.Gpio(REED_INNER_PIN)
    deny_button = mraa.Gpio(DENY_PIN)
    approve_button = mraa.Gpio(APPROVE_PIN)
    prev_success = 0
    prev_failure = 0
    prev_approve = 0
    prev_doorbell = 0
    prev_deny = 0

    # Set direction of GPIO
    in_success.dir(mraa.DIR_IN)
    in_failure.dir(mraa.DIR_IN)
    in_status_0.dir(mraa.DIR_IN)
    in_status_1.dir(mraa.DIR_IN)
    doorbell.dir(mraa.DIR_IN)
    reed_outer.dir(mraa.DIR_IN)
    reed_inner.dir(mraa.DIR_IN)
    deny_button.dir(mraa.DIR_IN)
    approve_button.dir(mraa.DIR_IN)
    
    # Create Bluetooth connections to the RFduinos on the doors
    if DEBUG > 0:
        print 'Connecting to RFduinos...'
    inner_door = Peripheral(INNER_ADDR, 'random')
    outer_door = Peripheral(OUTER_ADDR, 'random')
    
    # Create handles to the Bluetooth read and write characteristics
    inner_r_ch = inner_door.getCharacteristics(uuid=READ_UUID)[0]
    inner_w_ch = inner_door.getCharacteristics(uuid=WRITE_UUID)[0]
    outer_r_ch = outer_door.getCharacteristics(uuid=READ_UUID)[0]
    outer_w_ch = outer_door.getCharacteristics(uuid=WRITE_UUID)[0]
    
    # Set up camera
    pygame.camera.init()
    cam = pygame.camera.Camera(pygame.camera.list_cameras()[0])
    
    # Connect to Twitter and start listening
    if DEBUG > 0:
        print 'Connecting to Twitter...'
    tf = TweetFeed({'app_key': APP_KEY, \
                    'app_secret': APP_SECRET, \
                    'oauth_token': OAUTH_TOKEN, \
                    'oauth_token_secret': OAUTH_TOKEN_SECRET})
    listen_thread = threading.Thread(target=createStreamer)
    listen_thread.daemon = True
    listen_thread.start()

    # Send a good morning Tweet
    tf.tweet(ONLINE_MSG)

    # Register 'ctrl+C' signal handler
    signal.signal(signal.SIGINT, signalHandler)

    # Main loop
    g_mainloop = True
    if DEBUG > 0:
        print 'Starting door'
    while g_mainloop:
        
        # Poll pins for success or failure (falling edge)
        state_success = in_success.read()
        state_failure = in_failure.read()
        state_doorbell = doorbell.read()
        state_deny = deny_button.read()
        state_approve = approve_button.read()
        
        # Look for success in access panel
        if (state_success == 0) and (prev_success == 1):
            openDoor(inner_door, inner_w_ch, reed_inner)
            person_ind = (2 * in_status_1.read()) + in_status_0.read()
            if person_ind == 0:
                if DEBUG > 0:
                    print 'Success!'
                    print 'No one in particular.'
                tf.tweet(SUCCESS_MSG)
            else:
                if DEBUG > 0:
                    print 'Success!'
                    print 'Person = ' + NAMES[person_ind - 1]
                tf.tweet(SUCCESS_MSG + NAMES[person_ind - 1])

        # Look for failure in access panel
        elif (state_failure == 0) and (prev_failure == 1):
            if DEBUG > 0:
                print 'Fail.'
            tf.tweetPhoto(cam, FAILURE_MSG)
            
        # Look for doorbell push
        elif (state_doorbell == 0) and (prev_doorbell == 1):
            if DEBUG > 0:
                print 'Doorbell pressed.'
            openDoor(outer_door, outer_w_ch, reed_outer)
            
        # Look for deny button push
        elif (state_deny == 0) and (prev_deny == 1):
            if DEBUG > 0:
                print 'DENIED. Go away, and never come back.'
            openDoor(outer_door, outer_w_ch, reed_outer)
            
        # Look for an approve button push
        elif (state_approve == 0) and (prev_approve == 1):
            if DEBUG > 0:
                print 'APPROVED. You may enter.'
            openDoor(inner_door, inner_w_ch, reed_inner)
        
        prev_success = state_success
        prev_failure = state_failure
        prev_doorbell = state_doorbell
        prev_deny = state_deny
        prev_approve = state_approve

        # See if we have a command from Twitter
        if g_command == TWEET_LET_IN:
            openDoor(inner_door, inner_w_ch, reed_inner)
            g_command = TWEET_CLEAR
        elif g_command == TWEET_LET_OUT:
            openDoor(outer_door, outer_w_ch, reed_outer)
            g_command = TWEET_CLEAR

        # Wait a bit before next cycle
        time.sleep(0.01)
                
    # Outside of main loop. Cleanup and cuddles.
    if DEBUG > 0:
        print 'Cleaning up.'
    g_streamer.stop()
    listen_thread.join(None)
    del listen_thread

    pygame.camera.quit()
    pygame.quit()
                
# Run main
main()
