# Copyright (C) 2018 British Broadcasting Corporation
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

import os
import json
import time
import requests
import git
import jsonschema

from jsonschema import ValidationError, RefResolver, Draft4Validator
from Specification import Specification
from TestHelper import UTC_LEAP
from TestResult import Test

# TODO: Consider whether to set Accept headers? If we don't set them we expect APIs to default to application/json
# unless told otherwise. Is this part of the spec?


class GenericTest(object):
    """
    Generic testing class.
    Can be inhereted from in order to perform detailed testing.
    """
    def __init__(self, base_url, apis, spec_versions, test_version, spec_path):
        self.base_url = base_url
        self.apis = apis
        self.spec_versions = spec_versions
        self.test_version = test_version
        self.spec_path = spec_path
        self.file_prefix = "file:///" if os.name == "nt" else "file:"
        self.saved_entities = {}

        self.major_version, self.minor_version = self._parse_version(self.test_version)

        repo = git.Repo(self.spec_path)
        self.result = list()

        spec_branch = self.test_version + ".x"
        repo.git.reset('--hard')
        repo.git.checkout(spec_branch)
        self.parse_RAML()

    def _parse_version(self, version):
        """Parse a string based API version into its major and minor numbers"""
        version_parts = version.strip("v").split(".")
        return int(version_parts[0]), int(version_parts[1])

    def parse_RAML(self):
        """Create a Specification object for each API defined in this object"""
        for api in self.apis:
            self.apis[api]["spec"] = Specification(os.path.join(self.spec_path + '/APIs/' + self.apis[api]["raml"]))

    def execute_tests(self):
        """Perform all tests defined within this class"""
        print(" * Running basic API tests")
        self.result += self.basics()
        for method_name in dir(self):
            if method_name.startswith("test_"):
                method = getattr(self, method_name)
                if callable(method):
                    print(" * Running " + method_name)
                    self.result.append(method())

    def run_tests(self):
        """Perform tests and return the results as a list"""
        self.execute_tests()
        return self.result

    def convert_bytes(self, data):
        """Convert bytes which may be contained within a dict or tuple into strings"""
        if isinstance(data, bytes):
            return data.decode('ascii')
        if isinstance(data, dict):
            return dict(map(self.convert_bytes, data.items()))
        if isinstance(data, tuple):
            return map(self.convert_bytes, data)
        return data

# Tests: Schema checks for all resources
# CORS checks for all resources
# Trailing slashes

    def prepare_CORS(self, method):
        """Prepare CORS headers to be used when making any API request"""
        headers = {}
        headers['Access-Control-Request-Method'] = method  # Match to request type
        headers['Access-Control-Request-Headers'] = "Content-Type"  # Needed for POST/PATCH etc only
        return headers

    def validate_CORS(self, method, response):
        """Check the CORS headers returned by an API call"""
        if 'Access-Control-Allow-Origin' not in response.headers:
            return False
        if method in ['OPTIONS', 'POST', 'PUT', 'PATCH', 'DELETE']:
            if 'Access-Control-Allow-Headers' not in response.headers:
                return False
            if method not in response.headers['Access-Control-Allow-Headers']:
                return False
            if 'Access-Control-Allow-Method' not in response.headers:
                return False
            if method not in response.headers['Access-Control-Allow-Methods']:
                return False
        return True

    def check_base_path(self, path, expectation):
        """Check that a GET to a path returns a JSON array containing a defined string"""
        test = Test("GET {}".format(path))
        req = requests.get(self.base_url + path)
        if req.status_code != 200:
            return test.FAIL("Incorrect response code: {}".format(req.status_code))
        elif not self.validate_CORS('GET', req):
            return test.FAIL("Incorrect CORS headers: {}".format(req.headers))
        else:
            try:
                if not isinstance(req.json(), list) or expectation not in req.json():
                    return test.FAIL("Response is not an array containing '{}'".format(expectation))
                else:
                    return test.PASS()
            except json.decoder.JSONDecodeError:
                return test.FAIL("Non-JSON response returned")

    def check_response(self, test, api_name, method, path, response):
        """Confirm that a given Requests response conforms to the expected schema and has any expected headers"""
        if not self.validate_CORS(method, response):
            return test.FAIL("Incorrect CORS headers: {}".format(response.headers))

        schema = self.apis[api_name]["spec"].get_schema(method, path, response.status_code)

        if schema:
            try:
                resolver = jsonschema.RefResolver(self.file_prefix + os.path.join(self.spec_path + '/APIs/schemas/'),
                                                  schema)
                jsonschema.validate(response.json(), schema, resolver=resolver)
            except jsonschema.ValidationError:
                return test.FAIL("Response schema validation error")
            except json.decoder.JSONDecodeError:
                return test.FAIL("Invalid JSON received")
        else:
            return test.MANUAL("Test suite unable to locate schema")

        return test.PASS()

    def do_request(self, method, url, data=None):
        """Perform a basic HTTP request with appropriate error handling"""
        try:
            s = requests.Session()
            req = None
            if data is not None:
                req = requests.Request(method, url, json=data)
            else:
                req = requests.Request(method, url)
            prepped = req.prepare()
            r = s.send(prepped)
            return True, r
        except requests.exceptions.Timeout:
            return False, "Connection timeout"
        except requests.exceptions.TooManyRedirects:
            return False, "Too many redirects"
        except requests.exceptions.RequestException as e:
            return False, str(e)

    def basics(self):
        """Perform basic API read requests (GET etc.) relevant to all API definitions"""
        results = []

        for api in self.apis:
            results.append(self.check_base_path("/", "x-nmos/"))
            results.append(self.check_base_path("/x-nmos", api + "/"))
            results.append(self.check_base_path("/x-nmos/{}".format(api), self.test_version + "/"))

            for resource in self.apis[api]["spec"].get_reads():
                for response_code in resource[1]['responses']:
                    if response_code == 200:
                        result = self.check_api_resource(resource, response_code, api)
                        if result is not None:
                            results.append(result)

        return results
        # TODO: For any method we can't test, flag it as a manual test
        # TODO: Write a harness for each write method with one or more things to send it. Test them using this as part
        #       of this loop
        # TODO: Equally test for each of these if the trailing slash version also works and if redirects are used on
        #       either.

    def check_api_resource(self, resource, response_code, api):
        # Test URLs which include a {resourceId} or similar parameter
        if resource[1]['params'] and len(resource[1]['params']) == 1:
            path_parts = resource[0].split("/")
            path = ""
            for part in path_parts:
                if part.startswith("{"):
                    break
                if part != "":
                    path += "/" + part
            if path in self.saved_entities:
                # Pick the first relevant saved entity and construct a test
                entity = self.saved_entities[path][0]
                url_param = resource[0].replace("{" + resource[1]['params'][0].name + "}", entity)
                url = "{}{}".format(self.apis[api]["url"].rstrip("/"), url_param)
                test = Test("{} /x-nmos/{}/{}{}".format(resource[1]['method'].upper(),
                                                        api,
                                                        self.test_version,
                                                        url_param))
            else:
                # There were no saved entities found, so we can't test this parameterised URL
                test = Test("{} /x-nmos/{}/{}{}".format(resource[1]['method'].upper(),
                                                        api,
                                                        self.test_version,
                                                        resource[0]))
                return test.NA("No resources found to perform this test")

        # Test general URLs with no parameters
        elif not resource[1]['params']:
            url = "{}{}".format(self.apis[api]["url"].rstrip("/"), resource[0])
            test = Test("{} /x-nmos/{}/{}{}".format(resource[1]['method'].upper(),
                                                    api,
                                                    self.test_version,
                                                    resource[0]))
        else:
            return None

        status, response = self.do_request(resource[1]['method'], url)
        if not status:
            return test.FAIL(response)

        if response.status_code != response_code:
            return test.FAIL("Incorrect response code: {}".format(response.status_code))

        # Gather IDs of sub-resources for testing of parameterised URLs...
        self.save_subresources(resource[0], response)

        return self.check_response(test, api, resource[1]["method"], resource[0], response)

    def save_subresources(self, path, response):
        """Get IDs contained within an array JSON response such that they can be interrogated individually"""
        subresources = list()
        try:
            if isinstance(response.json(), list):
                for entry in response.json():
                    # In general, lists return fully fledged objects which each have an ID
                    if isinstance(entry, dict) and "id" in entry:
                        subresources.append(entry["id"])
                    # In some cases lists contain strings which indicate the path to each resource
                    elif isinstance(entry, str) and entry.endswith("/"):
                        res_id = entry.rstrip("/")
                        subresources.append(res_id)
        except json.decoder.JSONDecodeError:
            pass

        if len(subresources) > 0:
            if path not in self.saved_entities:
                self.saved_entities[path] = subresources
            else:
                self.saved_entities[path] += subresources

    def getTAITime(self, offset=0.0):
        """Get the current TAI time as a colon seperated string"""
        myTime = time.time() + offset
        secs = int(myTime)
        nanos = int((myTime - secs) * 1e9)
        ippTime = self.from_UTC(secs, nanos)
        return str(ippTime[0]) + ":" + str(ippTime[1])

    def from_UTC(self, secs, nanos, is_leap=False):
        """Convert a UTC time into a TAI time"""
        leap_sec = 0
        for tbl_sec, tbl_tai_sec_minus_1 in UTC_LEAP:
            if secs >= tbl_sec:
                leap_sec = (tbl_tai_sec_minus_1 + 1) - tbl_sec
                break
        return secs + leap_sec + is_leap, nanos

    def load_schema(self, path):
        """Used to load in schemas"""
        real_path = os.path.join(self.spec_path + '/APIs/schemas/', path)
        f = open(real_path, "r")
        return json.loads(f.read())

    def compare_to_schema(self, schema, endpoint, status_code=200):
        """Compares the response from an endpoint to a schema"""
        resolver = RefResolver(self.file_prefix + os.path.join(self.spec_path + '/APIs/schemas/'), schema)
        valid, response = self.checkCleanRequest("GET", endpoint, code=status_code)
        if valid:
            try:
                Draft4Validator(schema).validate(response)
                return True, ""
            except ValidationError as e:
                return False, "Response from {} did not meet schema: {}".format(endpoint, str(e))
        else:
            return False, "Invalid response while getting data: " + response

    def checkCleanRequest(self, method, dest, data=None, code=200):
        """Checks a request can be made and the resulting json can be parsed"""
        status, response = self.do_request(method, self.url + dest, data)
        if not status:
            return status, response

        message = "Expected status code {} from {}, got {}.".format(code, dest, response.status_code)
        if response.status_code == code:
            try:
                return True, response.json()
            except:
                # Failed parsing JSON
                msg = "Failed decoding JSON from {}, got {}. Please check JSON syntax".format(
                    dest,
                    response.text
                )
                return False, msg
        else:
            return False, message
