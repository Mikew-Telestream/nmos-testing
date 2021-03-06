# Copyright (C) 2019 Advanced Media Workflow Association
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import re
import time
import json

from .. import Config as CONFIG
from ..GenericTest import GenericTest, NMOSTestException
from ..IS04Utils import IS04Utils
from ..IS05Utils import IS05Utils
from ..IS07Utils import IS07Utils

from ..TestHelper import WebsocketWorker

EVENTS_API_KEY = "events"
NODE_API_KEY = "node"
CONN_API_KEY = "connection"

# Number of seconds expected between heartbeats from IS-07 WebSocket Receivers
WS_HEARTBEAT_INTERVAL = 5

# Number of seconds without heartbeats before an IS-07 WebSocket Sender closes a connection
WS_TIMEOUT = 12


class IS0702Test(GenericTest):
    """
    Runs IS-07-02-Test
    """
    def __init__(self, apis):
        # Don't auto-test /transportfile as it is permitted to generate a 404 when master_enable is false
        omit_paths = [
            "/single/senders/{senderId}/transportfile"
        ]
        GenericTest.__init__(self, apis, omit_paths)
        self.events_url = self.apis[EVENTS_API_KEY]["url"]
        self.connection_url = self.apis[CONN_API_KEY]["url"]
        self.node_url = self.apis[NODE_API_KEY]["url"]
        self.is04_utils = IS04Utils(self.node_url)
        self.is05_utils = IS05Utils(self.connection_url)
        self.is07_utils = IS07Utils(self.events_url)

    def set_up_tests(self):
        self.is05_senders = self.is05_utils.get_senders()
        self.is07_sources = self.is07_utils.get_sources_states_and_types()
        self.is04_sources = self.is04_utils.get_sources()
        self.is04_flows = self.is04_utils.get_flows()
        self.is04_senders = self.is04_utils.get_senders()
        self.transport_types = {}
        self.senders_active = {}
        self.senders_to_test = {}
        self.sources_to_test = {}

        for sender in self.is04_senders:
            flow = self.is04_senders[sender]["flow_id"]
            if flow in self.is04_flows:
                source = self.is04_flows[flow]["source_id"]
                if source in self.is04_sources:
                    if 'event_type' in self.is04_sources[source]:
                        self.senders_to_test[sender] = self.is04_senders[sender]
                        self.sources_to_test[source] = self.is04_sources[source]

        for sender in self.is05_senders:
            if self.is05_utils.compare_api_version(self.apis[CONN_API_KEY]["version"], "v1.1") >= 0:
                self.transport_types[sender] = self.is05_utils.get_transporttype(sender, "sender")
            else:
                self.transport_types[sender] = "urn:x-nmos:transport:rtp"

        if len(self.is05_senders) > 0:
            for sender in self.is05_senders:
                dest = "single/senders/" + sender + "/active"
                valid, response = self.is05_utils.checkCleanRequestJSON("GET", dest)
                if valid:
                    if len(response) > 0 and isinstance(response["transport_params"][0], dict):
                        self.senders_active[sender] = response

    def test_01(self, test):
        """Each IS-05 Sender has the required ext parameters"""

        ext_params_websocket = ['ext_is_07_source_id', 'ext_is_07_rest_api_url']
        ext_params_mqtt = ['ext_is_07_rest_api_url']
        if len(self.senders_to_test.keys()) > 0:
            for sender in self.senders_to_test:
                if sender in self.senders_active:
                    all_params = self.senders_active[sender]["transport_params"][0].keys()
                    params = [param for param in all_params if param.startswith("ext_")]
                    valid_params = False
                    if self.transport_types[sender] == "urn:x-nmos:transport:websocket":
                        if sorted(params) == sorted(ext_params_websocket):
                            valid_params = True
                    elif self.transport_types[sender] == "urn:x-nmos:transport:mqtt":
                        if sorted(params) == sorted(ext_params_mqtt):
                            valid_params = True
                    if not valid_params:
                        return test.FAIL("Missing required ext parameters for Sender {}".format(sender))
                else:
                    return test.FAIL("Sender {} not found in Connection API".format(sender))
            return test.PASS()
        else:
            return test.UNCLEAR("Not tested. No resources found.")

    def test_02(self, test):
        """Each IS-07 Source has corresponding resources in IS-04 and IS-05"""

        api = self.apis[EVENTS_API_KEY]

        if len(self.is07_sources) > 0:
            warn_topic = False
            warn_message = ""
            for source_id in self.is07_sources:
                if source_id in self.sources_to_test:
                    found_source = self.sources_to_test[source_id]
                    try:
                        if found_source["format"] != "urn:x-nmos:format:data":
                            return test.FAIL("Source {} specifies an unsupported format in IS-04: {}"
                                             .format(found_source["id"], found_source["format"]))
                        if found_source["event_type"] != self.is07_sources[source_id]["state"]["event_type"]:
                            return test.FAIL("Source {} specifies a different event_type in IS-04"
                                             .format(found_source["id"]))
                    except KeyError as e:
                        return test.FAIL("Source {} does not contain expected key: {}"
                                         .format(found_source["id"], e))
                    found_sender = None
                    for sender_id in self.senders_to_test:
                        flow_id = self.senders_to_test[sender_id]["flow_id"]
                        if flow_id in self.is04_flows:
                            if source_id == self.is04_flows[flow_id]["source_id"]:
                                found_flow = self.is04_flows[flow_id]
                                found_sender = self.senders_to_test[sender_id]
                                break
                    if found_sender is not None:
                        try:
                            if found_flow["format"] != "urn:x-nmos:format:data":
                                return test.FAIL("Flow {} specifies an unsupported format: {}"
                                                 .format(found_flow["id"], found_flow["format"]))
                            if found_flow["media_type"] != "application/json":
                                return test.FAIL("Flow {} does not specify media_type 'application/json'"
                                                 .format(found_flow["id"]))
                            if found_flow["event_type"] != found_source["event_type"]:
                                return test.FAIL("Flow {} specifies a different event_type to the Source"
                                                 .format(found_flow["id"]))
                        except KeyError as e:
                            return test.FAIL("Flow {} does not contain expected key: {}"
                                             .format(found_flow["id"], e))

                        if found_sender["id"] in self.senders_active:
                            try:
                                params = self.senders_active[found_sender["id"]]["transport_params"][0]
                                if found_sender["transport"] == "urn:x-nmos:transport:websocket":
                                    if params["ext_is_07_source_id"] != source_id:
                                        return test.FAIL("IS-05 sender {} does not indicate the correct "
                                                         "'ext_is_07_source_id': {}"
                                                         .format(found_sender["id"], source_id))
                                elif found_sender["transport"] == "urn:x-nmos:transport:mqtt":
                                    topic = re.search("^x-nmos/events/(.+)/sources/(.+)$", params["broker_topic"])
                                    if not topic:
                                        warn_topic = True
                                        warn_message = "IS-05 sender {} does not follow the recommended convention " \
                                            "in 'broker_topic': {}".format(found_sender["id"], source_id)
                                    elif topic.group(2) != source_id:
                                        warn_topic = True
                                        warn_message = "IS-05 sender {} does not indicate the correct source " \
                                            "in 'broker_topic': {}".format(found_sender["id"], source_id)
                                    elif topic.group(1) != api["version"]:
                                        warn_topic = True
                                        warn_message = "IS-05 sender {} does not indicate the correct API version " \
                                            "in 'broker_topic': {}".format(found_sender["id"], api["version"])
                                else:
                                    return test.FAIL("IS-05 sender {} has an unsupported transport {}"
                                                     .format(found_sender["id"], found_sender["transport"]))
                            except KeyError as e:
                                return test.FAIL("Sender {} parameters do not contain expected key: {}"
                                                 .format(found_sender["id"], e))
                        else:
                            return test.FAIL("Source {} has no associated IS-05 sender".format(source_id))
                    else:
                        return test.FAIL("Source {} has no associated IS-04 sender".format(source_id))
                else:
                    return test.FAIL("Source {} not found in Node API".format(source_id))
            if warn_topic:
                return test.WARNING(warn_message,
                                    "https://amwa-tv.github.io/nmos-event-tally/branches/{}"
                                    "/docs/5.1._Transport_-_MQTT.html#32-broker_topic"
                                    .format(api["spec_branch"]))
            else:
                return test.PASS()
        else:
            return test.UNCLEAR("Not tested. No resources found.")

    def test_03(self, test):
        """WebSocket senders on the same device have the same connection_uri and connection_authorization parameters"""

        if len(self.is07_sources) > 0:
            found_senders = False
            senders_by_device = {}
            for source_id in self.is07_sources:
                if source_id in self.sources_to_test:
                    for sender_id in self.senders_to_test:
                        flow_id = self.senders_to_test[sender_id]["flow_id"]
                        if flow_id in self.is04_flows:
                            if source_id == self.is04_flows[flow_id]["source_id"]:
                                found_sender = self.senders_to_test[sender_id]
                                if found_sender["transport"] == "urn:x-nmos:transport:websocket":
                                    if found_sender["device_id"] not in senders_by_device:
                                        senders_dict = {}
                                        senders_dict[found_sender["id"]] = found_sender
                                        senders_by_device[found_sender["device_id"]] = senders_dict
                                    else:
                                        senders_dict = senders_by_device[found_sender["device_id"]]
                                        senders_dict[found_sender["id"]] = found_sender

            for device_id in senders_by_device:
                device_connection_uri = None
                device_connection_authorization = None
                senders_dict = senders_by_device[device_id]
                for sender_id in senders_dict:
                    found_sender = senders_dict[sender_id]
                    if found_sender["id"] in self.senders_active:
                        found_senders = True
                        try:
                            params = self.senders_active[found_sender["id"]]["transport_params"][0]
                            sender_connection_uri = params["connection_uri"]
                            sender_connection_authorization = params["connection_authorization"]

                            if device_connection_uri is None:
                                device_connection_uri = sender_connection_uri
                            else:
                                if device_connection_uri != sender_connection_uri:
                                    return test.FAIL("Sender {} does not have the same connection_uri "
                                                     "parameter within the same device"
                                                     .format(found_sender["id"]))
                            if device_connection_authorization is None:
                                device_connection_authorization = sender_connection_authorization
                            else:
                                if device_connection_authorization != sender_connection_authorization:
                                    return test.FAIL("Sender {} does not have the same "
                                                     "connection_authorization parameter within "
                                                     "the same device".format(found_sender["id"]))
                        except KeyError as e:
                            return test.FAIL("Sender {} parameters do not contain expected key: {}"
                                             .format(found_sender["id"], e))
                    else:
                        return test.FAIL("Source {} has no associated IS-05 sender".format(source_id))
            if found_senders:
                return test.PASS()
            else:
                return test.UNCLEAR("Not tested. No WebSocket sender resources found.")
        else:
            return test.UNCLEAR("Not tested. No resources found.")

    def test_04(self, test):
        """WebSocket connections lifecycle tests"""

        # Gather the possible connections and sources which can be subscribed to
        connection_sources = self.get_websocket_connection_sources(test)

        if len(connection_sources) > 0:
            websockets_no_health = {}
            websockets_with_health = {}
            for connection_uri in connection_sources:
                websockets_no_health[connection_uri] = WebsocketWorker(connection_uri)
                websockets_with_health[connection_uri] = WebsocketWorker(connection_uri)

            for connection_uri in websockets_no_health:
                websockets_no_health[connection_uri].start()

            for connection_uri in websockets_with_health:
                websockets_with_health[connection_uri].start()

            # Give each WebSocket client a chance to start and open its connection
            start_time = time.time()
            while time.time() < start_time + CONFIG.WS_MESSAGE_TIMEOUT:
                no_health_opened = all([websockets_no_health[_].is_open() for _ in websockets_no_health])
                with_health_opened = all([websockets_with_health[_].is_open() for _ in websockets_with_health])
                if no_health_opened and with_health_opened:
                    break
                time.sleep(0.2)

            # After that short while, they must all be connected successfully
            for websockets in [websockets_no_health, websockets_with_health]:
                for connection_uri in websockets:
                    websocket = websockets[connection_uri]
                    if websocket.did_error_occur():
                        return test.FAIL("Error opening WebSocket connection to {}: {}"
                                         .format(connection_uri, websocket.get_error_message()))
                    elif not websocket.is_open():
                        return test.FAIL("Error opening WebSocket connection to {}".format(connection_uri))

            # All WebSocket connections must stay open until a health command is required
            while time.time() < start_time + WS_HEARTBEAT_INTERVAL:
                for websockets in [websockets_no_health, websockets_with_health]:
                    for connection_uri in websockets:
                        websocket = websockets[connection_uri]
                        if not websocket.is_open():
                            return test.FAIL("WebSocket connection to {} was closed too early".format(connection_uri))
                time.sleep(1)

            # send health commands to one set of WebSockets
            health_command = {}
            health_command["command"] = "health"
            health_command["timestamp"] = self.is04_utils.get_TAI_time()

            for connection_uri in websockets_with_health:
                websockets_with_health[connection_uri].send(json.dumps(health_command))

            # All WebSocket connections which were sent a health command should respond with a health response
            while time.time() < start_time + WS_HEARTBEAT_INTERVAL * 2:
                if all([len(websockets_with_health[_].messages) >= 1 for _ in websockets_with_health]):
                    break
                time.sleep(0.2)

            for connection_uri in websockets_with_health:
                websocket = websockets_with_health[connection_uri]
                messages = websocket.get_messages()
                if len(messages) == 0:
                    return test.FAIL("WebSocket {} did not respond with a health response"
                                     "to the health command".format(connection_uri))
                elif len(messages) > 1:
                    return test.FAIL("WebSocket {} responded with more than 1 message"
                                     "to the health command".format(connection_uri))
                elif len(messages) == 1:
                    try:
                        message = json.loads(messages[0])
                        if "message_type" in message:
                            if message["message_type"] != "health":
                                return test.FAIL("WebSocket {} health response message_type is not "
                                                 "set to health but instead is {}"
                                                 .format(connection_uri, message["message_type"]))
                        else:
                            return test.FAIL("WebSocket {} health response"
                                             "does not have a message_type".format(connection_uri))
                        if "timing" in message:
                            if "origin_timestamp" in message["timing"]:
                                origin_timestamp = message["timing"]["origin_timestamp"]
                                if origin_timestamp != health_command["timestamp"]:
                                    return test.FAIL("WebSocket {} health response origin_timestamp is not "
                                                     "set to the original timestamp but instead is {}"
                                                     .format(connection_uri, origin_timestamp))
                            else:
                                return test.FAIL("WebSocket {} health response"
                                                 "does not have origin_timestamp".format(connection_uri))
                            if "creation_timestamp" in message["timing"]:
                                creation_timestamp = message["timing"]["creation_timestamp"]
                                if self.is04_utils.compare_resource_version(
                                        creation_timestamp, health_command["timestamp"]) != 1:
                                    return test.FAIL("WebSocket {} health response creation_timestamp expected to"
                                                     "be later than origin. creation_timestamp was {}"
                                                     .format(connection_uri, creation_timestamp))
                            else:
                                return test.FAIL("WebSocket {} health response"
                                                 "does not have creation_timestamp".format(connection_uri))
                        else:
                            return test.FAIL("WebSocket {} health response"
                                             "does not have a timing object".format(connection_uri))
                    except Exception as e:
                        return test.FAIL("WebSocket {} health response cannot be parsed"
                                         "exception {}".format(connection_uri, e))

            # All WebSocket connections which haven't been sent a health command must stay opened
            # for a period of time even without any heartbeats
            while time.time() < start_time + WS_TIMEOUT - 1:
                for connection_uri in websockets_no_health:
                    websocket = websockets_no_health[connection_uri]
                    if not websocket.is_open():
                        return test.FAIL("WebSocket connection (no health cmd sent) to {} was closed too early"
                                         .format(connection_uri))
                time.sleep(1)

            # A short while after that timeout period, and certainly before another IS-07 heartbeat
            # interval has passed, all WebSocket connections which haven't been sent a health command
            # should start being closed down and connections which have been sent a health command
            # should still remain opened
            while time.time() < start_time + WS_TIMEOUT + WS_HEARTBEAT_INTERVAL:
                for connection_uri in websockets_with_health:
                    websocket = websockets_with_health[connection_uri]
                    if not websocket.is_open():
                        return test.FAIL("WebSocket connection (health cmd sent) to {} was closed too early"
                                         .format(connection_uri))
                time.sleep(1)

            # Now, all WebSocket connections which haven't been sent a health command must all be disconnected
            for connection_uri in websockets_no_health:
                websocket = websockets_no_health[connection_uri]
                if websocket.is_open():
                    return test.FAIL("WebSocket connection (no health cmd sent) to {} was not closed after timeout"
                                     .format(connection_uri))

            # WebSocket connections which have been sent a health command should start being closed down now
            while time.time() < start_time + WS_TIMEOUT + WS_HEARTBEAT_INTERVAL * 2:
                if all([not websockets_with_health[_].is_open() for _ in websockets_with_health]):
                    break
                time.sleep(0.2)

            # Now, they must all be disconnected
            for connection_uri in websockets_with_health:
                websocket = websockets_with_health[connection_uri]
                if websocket.is_open():
                    return test.FAIL("WebSocket connection (health cmd sent) to {} was not closed after timeout"
                                     .format(connection_uri))

            return test.PASS()
        else:
            return test.UNCLEAR("Not tested. No resources found.")

    def get_websocket_connection_sources(self, test):
        """Returns a dictionary of WebSocket sources available for connection"""
        connection_sources = {}

        if len(self.is07_sources) > 0:
            for source_id in self.is07_sources:
                if source_id in self.sources_to_test:
                    for sender_id in self.senders_to_test:
                        flow_id = self.senders_to_test[sender_id]["flow_id"]
                        if flow_id in self.is04_flows:
                            if source_id == self.is04_flows[flow_id]["source_id"]:
                                found_sender = self.senders_to_test[sender_id]
                                if found_sender["transport"] == "urn:x-nmos:transport:websocket":
                                    if sender_id in self.senders_active:
                                        if not self.senders_active[sender_id]["master_enable"]:
                                            valid, response = self.is05_utils.perform_activation("sender", sender_id,
                                                                                                 masterEnable=True)
                                            if valid:
                                                self.senders_active[sender_id] = response
                                            else:
                                                raise NMOSTestException(test.FAIL(response))
                                        params = self.senders_active[sender_id]["transport_params"][0]
                                        if "connection_uri" not in params:
                                            raise NMOSTestException(test.FAIL("Sender {} has no connection_uri "
                                                                    "parameter".format(sender_id)))
                                        connection_uri = params["connection_uri"]
                                        if connection_uri not in connection_sources:
                                            connection_sources[connection_uri] = [source_id]
                                        else:
                                            connection_sources[connection_uri].append(source_id)

        return connection_sources
