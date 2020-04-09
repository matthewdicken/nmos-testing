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

from fractions import Fraction

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
        self.base_event_types = ["boolean", "string", "number"]

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

    def test_05(self, test):
        """WebSocket state messages tests"""

        # Gather the possible connections and sources which can be subscribed to
        connection_sources = self.get_websocket_connection_sources(test)

        if len(connection_sources) > 0:
            target_websockets = {}
            for connection_uri in connection_sources:
                target_websockets[connection_uri] = WebsocketWorker(connection_uri)

            for connection_uri in target_websockets:
                target_websockets[connection_uri].start()

            # Give each WebSocket client a chance to start and open its connection
            start_time = time.time()
            while time.time() < start_time + CONFIG.WS_MESSAGE_TIMEOUT:
                if all([target_websockets[_].is_open() for _ in target_websockets]):
                    break
                time.sleep(0.2)

            # After that short while, they must all be connected successfully
            for connection_uri in target_websockets:
                websocket = target_websockets[connection_uri]
                if websocket.did_error_occur():
                    return test.FAIL("Error opening WebSocket connection to {}: {}"
                                     .format(connection_uri, websocket.get_error_message()))
                elif not websocket.is_open():
                    return test.FAIL("Error opening WebSocket connection to {}".format(connection_uri))

            # All WebSocket connections must stay open until a health command is required
            # then we can check that we have not received any message
            while time.time() < start_time + WS_HEARTBEAT_INTERVAL:
                for connection_uri in target_websockets:
                    websocket = target_websockets[connection_uri]
                    messages = websocket.get_messages()
                    for message in messages:
                        try:
                            parsed_message = json.loads(message)
                            message_type = "Undefined"
                            if "message_type" in message:
                                message_type = parsed_message["message_type"]
                            if (message_type == "reboot" or message_type == "shutdown" or
                                    message_type == "connection_status"):
                                continue
                            return test.FAIL("WebSocket {} sent a message of type {} without any prior "
                                             "command, original message: {}"
                                             .format(connection_uri, message_type, message))
                        except KeyError as e:
                            return test.FAIL("WebSocket {} state response cannot be parsed "
                                             "exception {}, original message: {}"
                                             .format(connection_uri, e, message))
                time.sleep(1)

            # Test run 1
            self.websocket_state_messages_test_run(
                test, target_websockets, connection_sources, start_time + WS_HEARTBEAT_INTERVAL * 2, 1)

            # Test run 2 (will resend subscriptions)
            self.websocket_state_messages_test_run(
                test, target_websockets, connection_sources, start_time + WS_HEARTBEAT_INTERVAL * 3, 2)

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
                                            connection_sources[connection_uri] = [self.is04_sources[source_id]]
                                        else:
                                            connection_sources[connection_uri].append(self.is04_sources[source_id])
        return connection_sources

    def websocket_state_messages_test_run(self, test, target_websockets, connection_sources, end_time, run_number):
        """WebSocket state messages checks test run"""

        # Create health commands
        health_command = {}
        health_command["command"] = "health"
        health_command["timestamp"] = self.is04_utils.get_TAI_time()

        # Send health and subscription commands
        for connection_uri in target_websockets:
            subscription_command = {}
            subscription_command["command"] = "subscription"
            source_ids = [source["id"] for source in connection_sources[connection_uri]]
            subscription_command["sources"] = source_ids
            target_websockets[connection_uri].send(json.dumps(health_command))
            target_websockets[connection_uri].send(json.dumps(subscription_command))

        # All WebSocket connections which were sent commands should have responded
        while time.time() < end_time:
            if all([len(target_websockets[_].messages) >= 2 for _ in target_websockets]):
                break
            time.sleep(0.2)

        # Check all state messages
        for connection_uri in target_websockets:
            websocket = target_websockets[connection_uri]
            messages = websocket.get_messages()
            self.check_state_messages(test, messages, connection_sources, run_number)

    def check_state_messages(self, test, messages, connection_sources, subscription_command_counter):
        """Checks validity of received state messages"""

        sources_dictionary = {}
        sources_flows = {}
        sources_errors = {}
        for connection_uri in connection_sources:
            sources = connection_sources[connection_uri]
            for source in sources:
                source_id = source["id"]
                sources_dictionary[source_id] = source
                sources_flows[source_id] = {}
                for flow_id in self.is04_flows:
                    flow = self.is04_flows[flow_id]
                    if flow["source_id"] == source_id:
                        sources_flows[source_id][flow_id] = self.is04_flows[flow_id]
                sources_errors[source["id"]] = ("WebSocket {}, source {} did not have a matching state response "
                                                "after subscription command attempt number {}"
                                                .format(connection_uri, source["id"], subscription_command_counter))
        try:
            for message in messages:
                parsed_message = json.loads(message)
                if "message_type" in parsed_message:
                    message_type = parsed_message["message_type"]
                    if (message_type == "health" or
                            message_type == "reboot" or
                            message_type == "shutdown" or
                            message_type == "connection_status"):
                        continue
                    if message_type != "state":
                        raise NMOSTestException(
                            test.FAIL("WebSocket {} state response message_type is not "
                                      "set to state but instead is {}, original message: {}"
                                      .format(connection_uri, message_type, message)))
                else:
                    raise NMOSTestException(
                        test.FAIL("WebSocket {} response does not have a message_type, "
                                  "original message: {}"
                                  .format(connection_uri, message)))

                if "identity" in parsed_message:
                    identity = parsed_message["identity"]
                    if "source_id" in identity:
                        identity_source = identity["source_id"]
                        if identity_source in sources_dictionary:
                            if "flow_id" in identity:
                                identity_flow = identity["flow_id"]
                                if identity_source in sources_flows:
                                    flows = sources_flows[identity_source]
                                    if identity_flow in flows:
                                        del sources_errors[identity_source]  # Remove sources which are ok
                                        if "event_type" in parsed_message:
                                            if "payload" in parsed_message:
                                                self.check_event_payload(
                                                    test,
                                                    connection_uri,
                                                    sources_dictionary[identity_source],
                                                    parsed_message["event_type"],
                                                    parsed_message["payload"])
                                            else:
                                                raise NMOSTestException(
                                                    test.FAIL("WebSocket {} state response "
                                                              "does not have a payload, original message: {}"
                                                              .format(connection_uri, message)))
                                        else:
                                            raise NMOSTestException(
                                                test.FAIL("WebSocket {} state response "
                                                          "does not have an event_type, original message: {}"
                                                          .format(connection_uri, message)))
                                    else:
                                        raise NMOSTestException(
                                            test.FAIL("WebSocket {} state response identity flow_id {} "
                                                      "does not match id of any associated source flows, "
                                                      "for source id {}, original message: {}"
                                                      .format(connection_uri, identity_flow, identity_source, message)))
                                else:
                                    raise NMOSTestException(
                                        test.FAIL("WebSocket {} source {} "
                                                  "does not have any associated flows"
                                                  .format(connection_uri, identity_source)))
                            else:
                                raise NMOSTestException(
                                    test.FAIL("WebSocket {} state response identity does not have a flow_id, "
                                              "original message: {}"
                                              .format(connection_uri, message)))
                        else:
                            raise NMOSTestException(
                                test.FAIL("WebSocket {} state response is for an unknown source, "
                                          "original message: {}"
                                          .format(connection_uri, message)))
                    else:
                        raise NMOSTestException(
                            test.FAIL("WebSocket {} state response identity does not have a source_id, "
                                      "original message: {}"
                                      .format(connection_uri, message)))
                else:
                    raise NMOSTestException(
                        test.FAIL("WebSocket {} state response does not have identity, original message: {}"
                                  .format(connection_uri, message)))
                if "timing" in parsed_message:
                    timing = parsed_message["timing"]
                    if "creation_timestamp" not in timing:
                        raise NMOSTestException(
                            test.FAIL("WebSocket {} state response does not have a creation_timestamp, "
                                      "original message: {}"
                                      .format(connection_uri, message)))
                else:
                    raise NMOSTestException(
                        test.FAIL("WebSocket {} state response does not have timing, original message: {}"
                                  .format(connection_uri, message)))
        except KeyError as e:
            raise NMOSTestException(
                test.FAIL("WebSocket {} state response cannot be parsed exception {}, original message: {}"
                          .format(connection_uri, e, message)))
        for source in sources_errors:
            raise NMOSTestException(test.FAIL(sources_errors[source]))

    def check_event_payload(self, test, connection_uri, source, event_type, payload):
        """Checks validity of event payload"""

        source_id = source["id"]
        source_event_type = source["event_type"]

        str_payload = json.dumps(payload)

        if source_id not in self.is07_sources:
            raise NMOSTestException(
                test.FAIL("WebSocket {}, source {} did not have a matching REST type"
                          .format(connection_uri, source_id)))

        source_type = self.is07_sources[source_id]["type"]

        if source_event_type != event_type:
            raise NMOSTestException(
                test.FAIL("WebSocket {} state response payload event_type {} does not match "
                          "source {} event_type {}, original payload: {}"
                          .format(connection_uri, event_type, source_id, source_event_type, str_payload)))

        event_types_split = event_type.split("/")
        base_event_type = event_types_split[0]

        if base_event_type in self.base_event_types:
            if "value" not in payload:
                raise NMOSTestException(
                    test.FAIL("WebSocket {}, source id: {} state response payload "
                              "does not have a value, original payload: {}"
                              .format(connection_uri, source_id, str_payload)))
            value = payload["value"]
            if base_event_type == "boolean":
                if not isinstance(value, bool):
                    raise NMOSTestException(
                        test.FAIL("WebSocket {}, source id: {} state response payload "
                                  "for boolean event type is not a valid boolean, original payload: {}"
                                  .format(connection_uri, source_id, str_payload)))
            elif base_event_type == "string":
                if not isinstance(value, str):
                    raise NMOSTestException(
                        test.FAIL("WebSocket {}, source id: {} state response payload "
                                  "for string event type is not a valid string, original payload: {}"
                                  .format(connection_uri, source_id, str_payload)))
                if "min_length" in source_type:
                    if not isinstance(source_type["min_length"], int):
                        raise NMOSTestException(
                            test.FAIL("WebSocket {}, source id: {} type for string event type, does not have "
                                      "a valid min_length, original payload: {}"
                                      .format(connection_uri, source_id, str_payload)))
                    else:
                        if len(value) < source_type["min_length"]:
                            raise NMOSTestException(
                                test.FAIL("WebSocket {}, source id: {} response payload value length for string event "
                                          "type is less than the min_length {} defined in the type definition, "
                                          "original payload: {}"
                                          .format(connection_uri, source_id, source_type["min_length"], str_payload)))
                if "max_length" in source_type:
                    if not isinstance(source_type["max_length"], int):
                        raise NMOSTestException(
                            test.FAIL("WebSocket {}, source id: {} type for string event type, type does not have "
                                      "a valid max_length, original payload: {}"
                                      .format(connection_uri, source_id, str_payload)))
                    else:
                        if len(value) > source_type["max_length"]:
                            raise NMOSTestException(
                                test.FAIL("WebSocket {}, source id: {} response payload value length for string event "
                                          "type is greater than the max_length {} defined in the type definition, "
                                          "original payload: {}"
                                          .format(connection_uri, source_id, source_type["max_length"], str_payload)))
            elif base_event_type == "number":
                try:
                    if not isinstance(value, int):
                        raise NMOSTestException(
                                test.FAIL("WebSocket {}, source id: {} state response payload for number event type "
                                          "is not a a valid number, original payload: {}"
                                          .format(connection_uri, source_id, str_payload)))
                    if "scale" in payload:
                        if not isinstance(payload["scale"], int):
                            raise NMOSTestException(
                                test.FAIL("WebSocket {}, source id: {} state response payload for number event type "
                                          "does not have a valid scale, original payload: {}"
                                          .format(connection_uri, source_id, str_payload)))
                        # check value is between min and max if defined
                        fraction_value = Fraction(value, payload["scale"])
                        if "min" in source_type:
                            fraction_min = Fraction(source_type["min"]["value"], (source_type["min"]["scale"]))
                            self.check_value_greater_than_threshold(
                                test, connection_uri, source_id, str_payload, fraction_value, fraction_min)
                        if "max" in source_type:
                            fraction_max = Fraction(source_type["max"]["value"], (source_type["max"]["scale"]))
                            self.check_value_less_than_threshold(
                                test, connection_uri, source_id, str_payload, fraction_value, fraction_max)
                    else:
                        # check value is between min and max if defined
                        if "min" in source_type:
                            self.check_value_greater_than_threshold(
                                test, connection_uri, source_id, str_payload, value, source_type["min"]["value"])
                        if "max" in source_type:
                            self.check_value_less_than_threshold(
                                test, connection_uri, source_id, str_payload, value, source_type["max"]["value"])
                except KeyError as e:
                    raise NMOSTestException(
                        test.FAIL("WebSocket {} state response payload cannot be parsed "
                                  "exception {}, original payload: {}"
                                  .format(connection_uri, e, str_payload)))
            if "enum" in event_types_split:
                try:
                    valuesTypes = source_type["values"]
                    valueMatches = False
                    for valueType in valuesTypes:
                        if valueType["value"] == value:
                            valueMatches = True
                            break
                    if not valueMatches:
                        raise NMOSTestException(
                            test.FAIL("WebSocket {}, source id: {} state response payload value for enum "
                                      "event type, does not match any of the values defined "
                                      "in the type definition, original payload: {}"
                                      .format(connection_uri, source_id, str_payload)))
                except KeyError as e:
                    raise NMOSTestException(
                        test.FAIL("WebSocket {} state response payload cannot be parsed exception {}, "
                                  "original payload: {}"
                                  .format(connection_uri, e, str_payload)))

        else:
            raise NMOSTestException(test.FAIL("WebSocket {}, source id: {} state response event_type {} "
                                              "does not inherit from a known base type, original payload: {}"
                                              .format(connection_uri, source_id, event_type, str_payload)))

    def check_value_greater_than_threshold(self, test, connection_uri, source_id, str_payload, value, threshold):
        """Checks value is greater than threshold"""
        if value < threshold:
            raise NMOSTestException(
                test.FAIL("WebSocket {}, source id: {} state response payload value for number "
                          "event type is less than the min value {} "
                          "defined in the type definition, original payload: {}"
                          .format(connection_uri, source_id, threshold, str_payload)))

    def check_value_less_than_threshold(self, test, connection_uri, source_id, str_payload, value, threshold):
        """Checks value is less than threshold"""
        if value > threshold:
            raise NMOSTestException(
                test.FAIL("WebSocket {}, source id: {} state response payload value for number "
                          "event type is greater than the max value {} "
                          "defined in the type definition, original payload: {}"
                          .format(connection_uri, source_id, threshold, str_payload)))
