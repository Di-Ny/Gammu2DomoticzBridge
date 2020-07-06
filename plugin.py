# Connector between Domoticz and Gammu : easy gammu configuration, and periodic network test
# Date : 06/2020
# https://github.com/Di-Ny/
# Author: DiNy
#
#   Goal : 
#       - Configure Gammu with specific port, SIM, etc... 
#       - Check network connectivity, received SMS
#       - Command the Home Automation server 
#       - [LATER] Bridge to RaspiSMS
#
#   Concept : 
#       - Reconfigure the Gammu config file /home/pi/.gammurc with (Baudrate, Default number)
#       - Declare authorized phone numbers for SMS Control
#       - Makes use of a send_sms.sh, and call.sh scripts to prevent gammu process collision 
#       - 
#       - The heartbeat will check received SMS and Network connectivity (may be conflicting with RaspiSMS for SMS webhooks)
#       -  Notification based on priority. Prioriy High will be sent by SMS to the 1st user in list. Priority Emergency will be sent to everyone. 
#       -
#   Requirements : 
#       - Gammu must be installed and the config file created with the right permission for modifications
#       - GSM module must be wired to an UART 
#       - 
#
#   TODO :
#       - Use the Domoticz Transport Object instead of pyserial 
#       - Install Gammu and the required libraries of not already installed 
#       - Auto install SMS.sh script 
# 
# License : CC BY-NC-SA
"""
<plugin key="gammudz" name="Gammu-Domoticz Bridge" author="DiNy" version="0.0.1" wikilink="" externallink="">
    <description>
        <b>Gammu Domoticz Bridge</b><br/><br/>
        <b>Comptibility :</b>
        <ul style="list-style-type:square">
        <li>Goal: 0) Be independant of any internet connection (in case of failure). 1) Provide an independant notification system  2) Send control commands to the Domoticz server</li>
        <li>Domoticz V2020.2</li>
        <li>GSM Module : All supported by Gammu, please see https://fr.wammu.eu/gammu/ Tested on Sim800 series, Sim900 series</li>
        <li>GSM module is linked to a USB port to be recognized by both Gammu and Domoticz</li>
        <li>Commands can be sent to the server by <b>authorized phone numbers</b>, with a <b>password</b>, and associated to <b>IDX</b>'s. The following commands are available : "On", "Off", "Toggle", or an integer [0-100] for DimmingLevel (experimental)</li>
        </ul>
        <b>Parameters :</b>
        <ul style="list-style-type:square">
        <li>UART Port: please select a 'USB-like' port recognized by Domoticz</li>
        <li>Baudrate: Select the baudrate usually 9600 to 19200. If there are error, try to lower the baudrate. This have been tested up to 115200 baud.</li>
        <li>Pin number: to unlock sim card. Defaults are '1234' or '0000'. Leave empty if the number was cleared before. <b>Warning</b>: check twice your pin code, as it might lock the SIM card</li>
        <li>APN: Not used in SMS mode. Should be used in the near future to provide an internet access in case of Ethernet failure or No Ethernet.</li>
        <li>Authorized phone numbers: allowed to send commands to the server. The phone number start with the country code instead of the leadin '0': +33, +44, +34, etc. The same are used for the notification system (Priority High => 1st phone number only, Priority Emergency => All phone numbers notified) </li>
        <li>Password or key at the beginnning of a command. For example : 'cmd', or '1234' or any sercure key.</li>
        <li>Commands appairing. This field allows personnal name associated with IDX. It make SMS commands more friendly but you should remember the exact syntax of the command!</li>
        </ul>
         
        <b>Example SMS command:</b>
        <ul>
        <li>Authorized phone numbers: "+33601020304,+44601020304,+33701020304"</li>
        <li>Password or key: "1234"</li>
        <li>Commands: "Living fan:12,bedroom light:14"</li>
        </ul>
             =>Resulting SMS (send by +33601020304): "1234 Living fan On" will light the fan<br/>
             =>Resulting SMS (send by +33601020304):  "1234 bedroom light off" will shut down the bedroom light (IDX14)<br/>
             =>Resulting SMS (send by +33601020304):  "1234 bedroom light" will respond the actual state of the bedroom light<br/>
             =>Resulting SMS (send by +33601020304):  "1234 bedroom light 20" should dim the light to 20%<br/>
             <br/><br/>
    </description>
    <params>        
        <param field="SerialPort" label="GSM module UART port" required="true" default="/dev/ttyUSB_ttyS1" width="300px"/>
        <param field="Mode1" label="Baudrate" width="300px" required="true">
            <options>
                <option label="2400 baud" value="at2400" />
                <option label="4800 baud" value="at4800" />
                <option label="9600 baud" value="at9600" />
                <option label="19200 baud" value="at19200" />
                <option label="115200 baud" value="at115200" default="true"/>
                <option label="230400 baud" value="at230400" />
                <option label="460800 baud" value="at460800" />
            </options>
        </param>
        <param field="Mode2" label="Pin code of the SIMCard (optionnal)" width="300px" required="false"/>
        <param field="Mode3" label="APN (if not found) [NOT USED]" width="300px" required="false"/>
        <param field="Address" label="Athorized phone numbers for commands (with country code)*" required="true" width="300px" default="+33600000000,+44600000000"/>
        <param field="Mode4" label="Password or key for commands*" required="true" width="300px" default="cmd"/>
        <param field="Mode5" label="Commands appairing Name:IDX" required="false" width="300px" default="Name:IDX,Name:IDX,..."/>
        <param field="Mode6" label="Debug" width="75px" required="true">
            <options>
                <option label="true" value="true" />
                <option label="false" value="false" default="true"/>
            </options>
        </param>
    </params>
</plugin>
"""
import Domoticz
import json
import time
import datetime
import os
import psutil
import subprocess
import sys
from shutil import copy2
import re
from unidecode import unidecode
import requests

import serial
ser=None
SerialConn = None
hasConnected = False

#variable : unitID 
uid_GSMinfo=1
uid_SMS=2
uid_netstat=3
uid_jamming=4
#List Switch On state 
list_switch_On=['allumer','on','light','lightup','1','power']
list_switch_Off=['eteindre','off','lightoff','cutoff','0']
list_switch_Toggle=['toggle','togle','change','changer','basculer','invert','switch','inverser']

class BasePlugin:
    enabled = False

    def onStart(self):
        #Get the variables
        self.debugging = Parameters["Mode6"].strip()
        self.dt= str(datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        self.baudrate = Parameters["Mode1"].strip()
        self.pin = Parameters["Mode2"].strip()
        self.apn = Parameters["Mode3"].strip()
        self.auth_phones = Parameters["Address"].strip()
        self.port = Parameters["SerialPort"].strip()
        self.passkey = unidecode(Parameters["Mode4"].strip().replace(" ", "").lower())
        self.name_idx = unidecode(str(Parameters["Mode5"]).strip().replace(" ", "").lower())

        #HARDCODED
        if self.debugging == "true":
            Domoticz.Debugging(2)
        DumpConfigToLog()
        #Debug data values
        #HEartbeat 
        Domoticz.Heartbeat(20)
        Domoticz.Notifier("OnBoard_GSM")
        #ReConfigure the config.json 
        success = self.reWriteConfigFile()
        if not success:
            Domoticz.Log("Reconfigure config.json --> Failed")
            return 0
        #Create Variables and Devices
        if len(Devices) == 0:
            #SMS LOG Variable 
            Domoticz.Device(Name="GSM Info", TypeName="Text", Unit=uid_GSMinfo, DeviceID="gsm_info").Create()
            Domoticz.Device(Name="Received SMS", TypeName="Text", Unit=uid_SMS, DeviceID="gsm_receivedsms").Create()
            Domoticz.Device(Name="GSM Network Status", TypeName="Text", Unit=uid_netstat, DeviceID="gsm_attached").Create()
            Domoticz.Device(Name="GSM Jamming", TypeName="Alert", Unit=uid_jamming, DeviceID="gsm_jamming").Create()
        #PinCode if set 
        if self.pin != "":
            pin_status = os.popen('/usr/bin/gammu --config /home/pi/.gammurc entersecuritycode PIN '+self.pin).read()
            if "Nothing to enter." in pin_status:
                Domoticz.Log("No Pin Code required !")
            elif "Security error":
                Domoticz.Log("Error PIN Code ! Please check in a phone (locked after 3 attempts")
                return
        time.sleep(0.5)  
        # global SerialConn
        global ser
        ser = serial.Serial(port=self.port,baudrate=int(self.baudrate.split('at')[1]),timeout=1)
        # ser = serial.Serial(port = '/dev/ttyUSB_ttyS1',baudrate=19200,timeout=1)
        try: 
            ser.open()
            # SerialConn = Domoticz.Connection(Name="tty_GSM", Transport="Serial", Protocol="None", Address=self.port, Baud=int(self.baudrate))
            # SerialConn.Connect()
            # SerialConn.Send(b'AT+SJDR=1,0,255\r')
        except Exception as e:
            Domoticz.Debug( "error open serial port: " + str(e))
        #Set the jamming detection option
        ser.write(b'AT+SJDR=1,0,255\r')
        ser.close()

        #Check that everything is running fine
        network_info = os.popen('/usr/bin/gammu --config /home/pi/.gammurc networkinfo').read()
        if "Warning" in network_info or "Error" in network_info :
            success = 0
            
        if success == 1:
            #remove the backup file
            os.system("sudo rm "+self.backupfile)
        else:
            Domoticz.Log("Trying to revert the config file...")
            copy2(self.backupfile,self.path+"/config.json")
            success = self.startProcess()
        #Update the ID
        if success:
            #update the network info
            Devices[uid_GSMinfo].Update(sValue=str(network_info), nValue=0)
            # Devices["gsm_info"].Update(sValue=str(network_info), nValue=0)


    def reWriteConfigFile(self):
        #backup file 
        file = "/home/pi/.gammurc"
        backup = "/home/pi/.gammurc.bakDZ."+self.dt
        self.backupfile=backup
        copy2(file,backup)
        #open file 
        f= open(file,"r+")
        config_file = f.read()
        f.close()
        # Domoticz.Debug(str(config_file))
        #Re parameter
        end_look="\n"
        parameter=[["connection = ",self.baudrate],\
                    ["port = ",self.port]]
        for param in parameter:
            reg = "(?<=%s).*?(?=%s)" % (param[0],end_look)
            r = re.compile(reg,re.DOTALL)
            config_file = r.sub(param[1], config_file)

        #Save the new file 
        f = open(file, "w+")
        f.write(config_file)
        f.close()
        return 1

    
    def onStop(self):
        Domoticz.Log("Killing GAMMU process")
        os.system("sudo killall gammu")
        time.sleep(2)

    def onConnect(self, Connection, Status, Description):
        Domoticz.Debug("onConnect called")
        global ser
        # global SerialConn
        # if (Status == 0):
        #     Domoticz.Log("Connected successfully to GSM on "+Parameters["SerialPort"])
        #     Connection.Send("AT\r")
        #     SerialConn = Connection
        # else:
        #     Domoticz.Log("Failed to connect ("+str(Status)+") to: "+Parameters["SerialPort"])
        #     Domoticz.Debug("Failed to connect ("+str(Status)+") to: "+Parameters["SerialPort"]+" with error: "+Description)
        # return True

    def onMessage(self, Connection, Data):
        Domoticz.Debug("onMessage called")

    def onCommand(self, Unit, Command, Level, Hue):
        Domoticz.Debug("onCommand called for Unit " + str(Unit) + ": Parameter '" + str(Command) + "', Level: " + str(Level))

    def onNotification(self, Name, Subject, Text, Status, Priority, Sound, ImageFile):
        Domoticz.Debug("Notification: " + Name + "," + Subject + "," + Text + "," + Status + "," + str(Priority) + "," + Sound + "," + ImageFile)
        #Notification based on priority. Prioriy High will be sent by SMS to the 1st user in list. Priority Emergency will be sent to everyone. 
        for phs in self.auth_phones.split(','):
            if Priority > 0:
                #Send to every mobile phone in the list 
                Domoticz.Log('Notification sent to '+phs)
                os.system('sudo nohup /home/pi/domoticz/scripts/bash/send_sms.sh '+phs+' "Domoticz.'+Name+'" "'+Subject+'" &')
                if not (Priority>1):
                    return

    def onDisconnect(self, Connection):
        Domoticz.Debug("onDisconnect called")

    def onHeartbeat(self):
        # global hasConnected, SerialConn
        global ser
        PID = "NO PROCESS"
        #Check if a GAMMU process is running, delay 1, if still running probleme, stop and restart 
        i=0
        while i < 17:
            for proc in psutil.process_iter(['pid', 'name', 'username']):
                if proc.info['name']=='gammu':
                    PID = str(proc.info['pid'])
            if PID == "NO PROCESS":
                break
            i += 1
            time.sleep(1)

        if PID != "NO PROCESS":
            Domoticz.Log("Gammu seems stuck, restarting !")
            self.onStop()
            time.sleep(2)
            self.onStart()
        else:
            #Jamming 
            # if (SerialConn.Connected()):
            #     SerialConn.Send(b'AT+SJDR?\r')
            # else:
            #     hasConnected = False
            #     SerialConn.Connect()
            try: 
                ser.open()
            except Exception as e:
                Domoticz.Debug( "error open serial port: " + str(e))
            ser.write(b'AT+SJDR?\r')
            a=ser.readline().strip().decode('ascii')
            while a != '':
                if '+SJDR:' in a and len(a)>10:
                    jamming=a.split(',')[4].split('\r')[0]
                    jam_level = 0#domoticz alert level 
                    jam_text = "No jamming"#domoticz alert level 
                    if '2' in jamming:
                        jam_level=3
                        jam_text = "Interferences detected"
                    if '1' in jamming:
                        jam_level=4
                        jam_text = "Alert jamming !"
                    Devices[uid_jamming].Update(sValue=jam_text, nValue=jam_level)

                a=ser.readline().strip().decode('ascii')
            ser.close()
            #Network INFO [WIP] --> Add network status clearly
            network_info = os.popen('/usr/bin/gammu --config /home/pi/.gammurc networkinfo').read().strip()
            if "Warning" in network_info or "Error" in network_info :
                Devices[uid_GSMinfo].Update(sValue="Error with Gammu", nValue=0)
            else:
                Devices[uid_GSMinfo].Update(sValue=str(network_info), nValue=0)
                Devices[uid_netstat].Update(sValue=str(network_info.split('GPRS                 : ')[1].split('\n')[0]), nValue=0)


            #Get SMS
            sms = os.popen('/usr/bin/gammu --config /home/pi/.gammurc getallsms').read().strip()
            message_number = 0
            if '0 SMS parts in 0 SMS sequences' in sms:
                Domoticz.Debug('Pas de message reçu')
            else:
                Domoticz.Debug('Message reçu')
                new_message=True
                while new_message:
                    if 'Location' in sms:
                        message_number += 1
                        #split inot parts 
                        sms_parts = sms.split('\n')
                        #Gather elements
                        sms_date = sms_parts[3].split('Sent                 : ')[1].split(' +')[0]
                        sms_sender = sms_parts[5].split('Remote number        : "')[1].split('"')[0]
                        sms_cmd_raw = sms_parts[8]
                        sms_display = sms_date + '('+sms_sender+'):\n'+sms_cmd_raw
                        Devices[uid_SMS].Update(sValue=sms_display, nValue=0)
                        Domoticz.Log(sms_display)
                        #process message command 
                        if sms_sender in self.auth_phones.split(','):
                            sms_condensed = unidecode(str(sms_cmd_raw).strip().replace(" ", "").lower())
                            if self.passkey in sms_condensed:
                                Domoticz.Log('Proceed Command ')
                                answer = ''
                                sms_cmd_list = sms_condensed.split(str(self.passkey))[1].split('\n')
                                for sms_cmd in sms_cmd_list:
                                    #Look for known commands
                                    for n_idx in self.name_idx.split(','):
                                        #Check if Key is in the SMS
                                        key = str(n_idx.split(':')[0])
                                        key_idx=str(n_idx.split(':')[1])
                                        if key in sms_cmd:
                                            #Check if there is a command, or it is just a query for state
                                            if len(sms_cmd.split(key)) > 1 and sms_cmd.split(key)[1] != '':#['cmd', 'on']--> len()=2 or ['cmd','']
                                                #Associate command with Domoticz Command
                                                d_command=sms_cmd.split(key)[1].split('\n')[0]
                                                device_command=''
                                                if d_command in list_switch_On:
                                                    device_command='On'
                                                elif d_command in list_switch_Off:
                                                    device_command='Off'
                                                elif d_command in list_switch_Toggle:
                                                    device_command='Toggle'
                                                else:
                                                    #Dimmable LED
                                                    device_command='Set%20Level&level='+d_command
                                                #Set the command via HTTP API 
                                                r = requests.get('http://127.0.0.1:8080/json.htm?type=command&param=switchlight&idx='+key_idx+'&switchcmd='+device_command)
                                                if r.status_code == 200:
                                                    answer += 'Ok, device '+key+'(IDX: '+key_idx+') was set to '+device_command
                                                else:
                                                    answer += 'Problem with command ! code '+str(r.status_code)+': '+str(r.text)

                                            #Else just query the state 
                                            else:
                                                #Get the status of a specifi device via HTTP API 
                                                r = requests.get('http://127.0.0.1:8080/json.htm?type=devices&rid='+key_idx)
                                                if r.status_code == 200:
                                                    #parse the answered JSON
                                                    http_answ=json.loads(r.text)["result"][0]
                                                    #Print answer
                                                    answer += 'Device '+http_answ['Name']+' (IDX:'+http_answ['idx']+') is '+http_answ['Data']+' (last updated on '+http_answ['LastUpdate']+')'
                                                else:
                                                    answer += 'Problem with command ! code '+str(r.status_code)+': '+str(r.text)

                                    if 'restart' in sms_cmd:
                                        Domoticz.Log("System will reboot in 5 seconds")
                                        #SEND SMS .py
                                        #TODO
                                        os.system('sudo /home/pi/domoticz/scripts/bash/send_sms.sh '+sms_sender+' "Reboot now"')
                                        #
                                        time.sleep(1)
                                        r = requests.get('http://127.0.0.1:8080/json.htm?type=command&param=system_reboot')
                                Domoticz.Log(answer)
                                Domoticz.Debug('sudo nohup /home/pi/domoticz/scripts/bash/send_sms.sh '+sms_sender+' "'+answer+'" &')
                                #SMS.py --> Answer 
                                os.system('sudo nohup /home/pi/domoticz/scripts/bash/send_sms.sh '+sms_sender+' "'+answer+'" &')

                            else:
                                Domoticz.Log('Error: Command received, but phone number not registered. Authorized phones are :')
                                for phs in self.auth_phones.split(','):
                                    Domoticz.Log(str(phs))

                        #End of first SMS. Implode and start again
                        sms = "\n".join(sms_parts[10:])

                    else:
                        new_message = False
                Domoticz.Log(str(message_number)+' messages processed')
                #Delete all SMS
                del_sms = os.popen('/usr/bin/gammu --config /home/pi/.gammurc deleteallsms 1').read().strip()
                Domoticz.Log(str(del_sms))
            # onMessage()


global _plugin
_plugin = BasePlugin()

def onStart():
    global _plugin
    _plugin.onStart()

def onStop():
    global _plugin
    _plugin.onStop()

def onConnect(Connection, Status, Description):
    global _plugin
    _plugin.onConnect(Connection, Status, Description)

def onMessage(Connection, Data):
    global _plugin
    _plugin.onMessage(Connection, Data)

def onCommand(Unit, Command, Level, Hue):
    global _plugin
    _plugin.onCommand(Unit, Command, Level, Hue)

def onNotification(Name, Subject, Text, Status, Priority, Sound, ImageFile):
    global _plugin
    _plugin.onNotification(Name, Subject, Text, Status, Priority, Sound, ImageFile)

def onDisconnect(Connection):
    global _plugin
    _plugin.onDisconnect(Connection)

def onHeartbeat():
    global _plugin
    _plugin.onHeartbeat()

    # Generic helper functions
def DumpConfigToLog():
    for x in Parameters:
        if Parameters[x] != "":
            Domoticz.Debug( "'" + str(x) + "':'" + str(Parameters[x]) + "'")
    Domoticz.Debug("Device count: " + str(len(Devices)))
    for x in Devices:
        Domoticz.Debug("Device:           " + str(x) + " - " + str(Devices[x]))
        Domoticz.Debug("Device ID:       '" + str(Devices[x].ID) + "'")
        Domoticz.Debug("Device Name:     '" + Devices[x].Name + "'")
        Domoticz.Debug("Device nValue:    " + str(Devices[x].nValue))
        Domoticz.Debug("Device sValue:   '" + Devices[x].sValue + "'")
        Domoticz.Debug("Device LastLevel: " + str(Devices[x].LastLevel))
    return