import requests
import json
import time
import django.utils.text
import pprint

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import MultipleObjectsReturned
from django.contrib.contenttypes.models import ContentType
from datetime import datetime

from dcim.models import Device, DeviceType, Module, Manufacturer
from netbox_lifecycle.models import hardware, contract


class Command(BaseCommand):
    help = 'Sync Hardware Support from Cisco Services API (CSAPI)'

    PLUGIN_SETTINGS = settings.PLUGINS_CONFIG.get("netbox_lifecycle", dict())

    CISCO_SERVICES_API_CUSTOMER_ID = ''
    TOKEN_TIMER_START = None
    ASSOCIATE_UNCOVERED_HW_TO_UNCOVERED_CONTRACT = True
    UNCOVERED_CONTRACT_NAME = PLUGIN_SETTINGS.get("uncovered_contract_name", "SELF-SUPPORT" )
    UNCOVERED_CONTRACT_VENDOR = PLUGIN_SETTINGS.get("uncovered_contract_vendor", "SELF-SUPPORT")


    def add_arguments(self, parser):
        # Named (optional) arguments
        parser.add_argument(
            '--manufacturer',
            action='store_true',
            default='Cisco',
            help='Manufacturer name (default: Cisco)',
        )

    def api_logon(self):
        
        CISCO_CLIENT_ID = self.PLUGIN_SETTINGS.get("cisco_services_api_client_id", "")
        CISCO_CLIENT_SECRET = self.PLUGIN_SETTINGS.get("cisco_services_api_client_secret", "")
        self.CISCO_SERVICES_API_CUSTOMER_ID = self.PLUGIN_SETTINGS.get("cisco_services_api_customer_id", "")

        token_url = "https://id.cisco.com/oauth2/default/v1/token"
        data = {'grant_type': 'client_credentials', 'client_id': CISCO_CLIENT_ID, 'client_secret': CISCO_CLIENT_SECRET}

        access_token_response = requests.post(token_url, data=data)

        tokens = json.loads(access_token_response.text)

        api_call_headers = {'Authorization': 'Bearer ' + tokens['access_token'], 'Accept': 'application/json'}

        self.TOKEN_TIMER_START = time.perf_counter()
        print(tokens['expires_in'])

        return api_call_headers

    def check_manufacturer_support_sku(self, manufacturer_name, sku_name):

        # Query for the Manufacturer First
        try:
            manufacturer_results = Manufacturer.objects.get(name=manufacturer_name)
        except Manufacturer.DoesNotExist:
            raise CommandError( f'Manufacturer "{manufacturer_name}" does not exist' )
        
        # Check for the existance of the Support SKU in the DB
        try:
            support_sku = contract.SupportSKU.objects.get(manufacturer = manufacturer_results,
                                                                sku = sku_name)
            self.stdout.write(self.style.SUCCESS( f"{sku_name} - Exists as a Support SKU" ))
        # If not, create a new one associated with vendor
        except contract.SupportSKU.DoesNotExist:
            self.stdout.write(self.style.WARNING( f"{sku_name} - Does not exist as a Support SKU" ))
            # Get the Cisco Vendor
            support_sku = contract.SupportSKU(manufacturer = manufacturer_results,
                                              sku = sku_name)
            support_sku.save()

    def check_vendor_contract(self, support_contract_id, vendor_name, contract_start="", contract_end=""):
        # TODO: Account for changing dates on the contract and do an update
        # Check for the existance of the Support contract in the DB
        try:
            support_contract = contract.SupportContract.objects.get(contract_id = support_contract_id)
            self.stdout.write(self.style.SUCCESS( f"{support_contract_id} - Exists as a Support Contract" ))
        # If not, create a new one associated with vendor
        except contract.SupportContract.DoesNotExist:
            self.stdout.write(self.style.WARNING( f"{support_contract_id} - Does not exist as a Support Contract" ))
            # Get the Cisco Vendor
            vendor_object = contract.Vendor.objects.get(name = vendor_name)
            contract_start_date = datetime.strptime(contract_start, '%Y-%m-%d').date()
            contract_end_date = datetime.strptime(contract_end, '%Y-%m-%d').date()
            support_contract = contract.SupportContract(contract_id = support_contract_id, 
                                                        vendor = vendor_object,
                                                        start = contract_start_date,
                                                        end = contract_end_date)
            support_contract.save()

    def check_vendor(self, vendor_name):
        # Check for the existance of the Support Vendor in the DB
        try:
            support_contract = contract.Vendor.objects.get(name = vendor_name)
            self.stdout.write(self.style.SUCCESS( f"{vendor_name} - Exists as a Support Vendor" ))
        # If not, create a new one associated with vendor
        except contract.Vendor.DoesNotExist:
            self.stdout.write(self.style.WARNING( f"{vendor_name}  - Does not exist as a Support Vendor - Creating" ))
            # Get the Cisco Vendor
            vendor_object = contract.Vendor(name=vendor_name)
            vendor_object.save()

    def process_api_response(self, hw_serial, response_body):
        # Parse the response from api.
        if len(response_body["data"]) == 0:
            # No data returned from the Cisco API - Means we have no contract attached to the hardware
            self.check_vendor(self.UNCOVERED_CONTRACT_VENDOR)
            self.check_manufacturer_support_sku("Cisco", self.UNCOVERED_CONTRACT_NAME)
            self.check_vendor_contract(self.UNCOVERED_CONTRACT_NAME, self.UNCOVERED_CONTRACT_VENDOR)
            
            manufacturer_record = Manufacturer.objects.get(name="Cisco")
            device_record = Device.objects.filter(serial = hw_serial).first()
            support_contract_record = contract.SupportContract.objects.get(contract_id = self.UNCOVERED_CONTRACT_NAME)
            support_sku_record = contract.SupportSKU.objects.get(manufacturer = manufacturer_record,
                                                                    sku = self.UNCOVERED_CONTRACT_NAME)
            self.stdout.write(self.style.SUCCESS( f"Associating Contract:{self.UNCOVERED_CONTRACT_NAME} with {hw_serial}" ))
            support_contract_assign_record = contract.SupportContractAssignment( device=device_record,
                                                                                contract = support_contract_record,
                                                                                sku = support_sku_record)
            support_contract_assign_record.save()

        else:
            for contract_data in response_body["data"]:
                contract_number = contract_data["contractNumber"]
                service_level = contract_data["serviceLevel"]
                support_vendor = contract_data["lineCustomerName"]
                contract_start = contract_data["contractStartDate"]
                contract_end = contract_data["contractEndDate"]
                coverage_start = contract_data["coverageStartDate"]
                coverage_end = contract_data["coverageEndDate"]
                serial_number = contract_data["serialNumber"]

                installed_at_site_id = contract_data["installedatSiteId"]
                installed_at_address = f'{contract_data["installedatSiteName"]}\r\n{contract_data["installedatAddressLine1"]}\r\n{contract_data["installedatCity"]}  {contract_data["installedatState"]}  {contract_data["installedatPostalCode"]}'

                # Do checks on the components of the vendor contract
                self.check_vendor(support_vendor)
                self.check_manufacturer_support_sku("Cisco", service_level)
                self.check_vendor_contract(contract_number, support_vendor, contract_start, contract_end)

                # Check if we have a support contract association in place already
                device_record = Device.objects.filter(serial = serial_number).first()
                manufacturer_record = Manufacturer.objects.get(name="Cisco")
                support_contract_record = contract.SupportContract.objects.get(contract_id = contract_number)
                support_sku_record = contract.SupportSKU.objects.get(manufacturer = manufacturer_record,
                                                                    sku = service_level)
                try:
                    record_changed = False
                    support_contract_assignment = contract.SupportContractAssignment.objects.get( device=device_record,
                                                                                        contract = support_contract_record,
                                                                                        sku = support_sku_record, end = coverage_end )
                    try:
                        if not (support_contract_assignment.custom_field_data["installed_at_site_id"] == installed_at_site_id):
                            support_contract_assignment.custom_field_data["installed_at_site_id"] = installed_at_site_id
                            record_changed = True
                    except KeyError:
                        support_contract_assignment.custom_field_data["installed_at_site_id"] = installed_at_site_id
                        record_changed = True

                    try:
                        if not (support_contract_assignment.custom_field_data["installed_at_address"] == installed_at_address):
                            support_contract_assignment.custom_field_data["installed_at_address"] = installed_at_address
                            record_changed = True
                    except KeyError:
                        support_contract_assignment.custom_field_data["installed_at_address"] = installed_at_address
                        record_changed = True

                    if (record_changed == True):
                        self.stdout.write(self.style.WARNING( f"Custom Field data being updated" ))
                        support_contract_assignment.save()
                    self.stdout.write(self.style.WARNING( f"{contract_number} is already associated with {serial_number} - Not Creating new association" ))
                except contract.SupportContractAssignment.DoesNotExist:
                    # Create a contract association if it doesn't exist
                    self.stdout.write(self.style.SUCCESS( f"Associating Contract:{contract_number} with {serial_number}" ))
                    support_contract_assign_record = contract.SupportContractAssignment( device=device_record,
                                                                                        contract = support_contract_record,
                                                                                        sku = support_sku_record, end = coverage_end )
                    support_contract_assign_record.save()


    def update_contract_data(self, pid, hardware_type, eox_data):

        self.stdout.write(self.style.SUCCESS( f"{pid} - {hardware_type}" ))
        
        content_type = ContentType()

        match hardware_type:
            case "devicetype":
                hw_obj = DeviceType()
                content_type = ContentType.objects.get(app_label="dcim", model="devicetype")
                try:
                    # Get the device type object for the supplied PID
                    hw_obj = DeviceType.objects.get(part_number=pid)
                    hw_count = Device.objects.filter(device_type=hw_obj).count()
                    self.stdout.write(self.style.SUCCESS( f"{pid} - {hw_count} active devices" ))
                except MultipleObjectsReturned:
                    # Error if Netbox returns multiple duplicate PN's
                    self.stdout.write(self.style.NOTICE( f"ERROR: Multiple objects exist with Part Number {pid}" ))
                    return

            case "moduletype":
                hw_obj = ModuleType()
                content_type = ContentType.objects.get(app_label="dcim", model="moduletype")
                try:
                    # Get the device type object for the supplied PID
                    hw_obj = ModuleType.objects.get(part_number=pid)
                    hw_count = Module.objects.filter(module_type=hw_obj).count()
                    self.stdout.write(self.style.SUCCESS( f"{pid} - {hw_count} active moduletypes" ))
                except MultipleObjectsReturned:
                    # Error if Netbox returns multiple duplicate PN's
                    self.stdout.write(self.style.NOTICE( f"ERROR: Multiple objects exist with Part Number {pid}" ))
                    return
            
            case _:
                raise CommandError( f'Invalid hardware_type argument defined.' )
                exit
        

        # Check if a HardwareLifecycle record already exists
        try:
            hw_lifecycle = hardware.HardwareLifecycle.objects.get(assigned_object_id=hw_obj.id)
            self.stdout.write(self.style.SUCCESS( f"{pid} - has an existing NetBox hardware lifecycle record" ))
            if ((hw_count == 0) and (self.TRACK_ONLY_ACTIVE_PIDS)):
                self.stdout.write(self.style.NOTICE( f"{pid} - has no active hardware with this PID - We're tracking only active PIDs - Deleting Lifecycle record" ))
                hw_lifecycle.delete()
                return
        # If not, create a new one for this Device Type
        except hardware.HardwareLifecycle.DoesNotExist:
            if ((hw_count == 0) and (self.TRACK_ONLY_ACTIVE_PIDS)):
                self.stdout.write(self.style.NOTICE( f"{pid} - no active hardware with this PID - We're only tracking active PIDs - no Lifecycle record created" ))
                return
            else:
                hw_lifecycle = hardware.HardwareLifecycle(assigned_object_id=hw_obj.id, assigned_object_type_id=content_type.id)
                self.stdout.write(self.style.NOTICE( f"{pid} - has no existing NetBox hardware lifecycle record" ))

        # Only save if something has changed
        value_changed = False
        # Sale and Support End-of values both required for a lifecycle record
        end_of_sale_defined = False
        end_of_support_defined = False

        try:
            # Check if JSON contains EndOfSaleDate with a value defined
            if not eox_data["EOXRecord"][0]["EndOfSaleDate"]["value"]:
                self.stdout.write(self.style.NOTICE( f"{pid} - has no end_of_sale_date" ))
            else:
                end_of_sale_date_string = eox_data["EOXRecord"][0]["EndOfSaleDate"]["value"]
                # Cast this value to datetime.date object
                end_of_sale_date = datetime.strptime(end_of_sale_date_string, '%Y-%m-%d').date()
                self.stdout.write(self.style.SUCCESS( f"{pid} - end_of_sale_date: {end_of_sale_date}" ))
                # Check if our HardwareLifecycle object has a different date to that returned from api
                if hw_lifecycle.end_of_sale != end_of_sale_date:
                    hw_lifecycle.end_of_sale = end_of_sale_date
                    end_of_sale_defined = True
                    value_changed = True

        # Do nothing when JSON field does not exist
        except KeyError:
            self.stdout.write(self.style.NOTICE( f"{pid} - has no end_of_sale_date" ))


        try:
            if not eox_data["EOXRecord"][0]["EndOfSWMaintenanceReleases"]["value"]:
                self.stdout.write(self.style.NOTICE( f"{pid} - has no end_of_sw_maintenance_releases" ))
            else:
                end_of_maintenance_string = eox_data["EOXRecord"][0]["EndOfSWMaintenanceReleases"]["value"]
                end_of_maintenance = datetime.strptime(end_of_maintenance_string, '%Y-%m-%d').date()
                self.stdout.write(self.style.SUCCESS( f"{pid} - end_of_sw_maintenance_releases: {end_of_maintenance}" ))

                if hw_lifecycle.end_of_maintenance != end_of_maintenance:
                    hw_lifecycle.end_of_maintenance = end_of_maintenance
                    value_changed = True
        except KeyError:
            self.stdout.write(self.style.NOTICE( f"{pid} - has no end_of_sw_maintenance_releases" ))

        try:
            if not eox_data["EOXRecord"][0]["EndOfSecurityVulSupportDate"]["value"]:
                self.stdout.write(self.style.NOTICE( f"{pid} - has no end_of_security_vul_support_date" ))
            else:
                end_of_security_string = eox_data["EOXRecord"][0]["EndOfSecurityVulSupportDate"]["value"]
                end_of_security_date = datetime.strptime(end_of_security_string, '%Y-%m-%d').date()
                self.stdout.write(self.style.SUCCESS( f"{pid} - end_of_security_vul_support_date: {end_of_security_date}" ))

                if hw_lifecycle.end_of_security != end_of_security_date:
                    hw_lifecycle.end_of_security = end_of_security_date
                    value_changed = True
        except KeyError:
            self.stdout.write(self.style.NOTICE( f"{pid} - has no end_of_security_vul_support_date"))

        try:
            if not eox_data["EOXRecord"][0]["EndOfServiceContractRenewal"]["value"]:
                self.stdout.write(self.style.NOTICE( f"{pid} - has no end_of_service_contract_renewal" ))
            else:
                last_contract_date_string = eox_data["EOXRecord"][0]["EndOfServiceContractRenewal"]["value"]
                last_contract_date_date = datetime.strptime(last_contract_date_string, '%Y-%m-%d').date()
                self.stdout.write(self.style.SUCCESS( f"{pid} - end_of_service_contract_renewal: {last_contract_date_date}" ))

                if hw_lifecycle.last_contract_date != last_contract_date_date:
                    hw_lifecycle.last_contract_date = last_contract_date_date
                    value_changed = True
        except KeyError:
            self.stdout.write(self.style.NOTICE( f"{pid} - has no end_of_service_contract_renewal" ))

        try:
            if not eox_data["EOXRecord"][0]["LastDateOfSupport"]["value"]:
                self.stdout.write(self.style.NOTICE( f"{pid} - has no last_date_of_support" ))
            else:
                end_of_support_string = eox_data["EOXRecord"][0]["LastDateOfSupport"]["value"]
                end_of_support_date = datetime.strptime(end_of_support_string, '%Y-%m-%d').date()
                self.stdout.write(self.style.SUCCESS( f"{pid} - last_date_of_support: {end_of_support_date}" ))

                if hw_lifecycle.end_of_support != end_of_support_date:
                    hw_lifecycle.end_of_support = end_of_support_date
                    value_changed = True
                    end_of_support_defined = True
        except KeyError:
            self.stdout.write(self.style.NOTICE( f"{pid} - has no last_date_of_support" ))

        if (value_changed and end_of_sale_defined and end_of_support_defined):
            if (self.SET_MISSING_DATA_AS_END_OF_SUPPORT):
                # Check whether end of security is blank.  Use end_of_support value in that case
                if (hw_lifecycle.end_of_security == None):
                    hw_lifecycle.end_of_security = hw_lifecycle.end_of_support
                if (hw_lifecycle.end_of_maintenance == None):
                    hw_lifecycle.end_of_maintenance = hw_lifecycle.end_of_support
            # Save the record
            hw_lifecycle.save()

        return


            



    def get_device_serials(self, manufacturer):
        results = {}

        # Query for the Manufacturer First
        try:
            manufacturer_results = Manufacturer.objects.get(name=manufacturer)
        except Manufacturer.DoesNotExist:
            raise CommandError( f'Manufacturer "{manufacturer}" does not exist' )

        self.stdout.write(self.style.SUCCESS( f'Found manufacturer "{manufacturer_results}"' ))

        # trying to get all device types associated with this manufacturer
        try:
            devicetype_results = DeviceType.objects.filter(manufacturer=manufacturer_results)
            for devicetype in devicetype_results:
                # Get the count of devices with this device_type
                hw_count = Device.objects.filter(device_type=devicetype).count()
                self.stdout.write(self.style.SUCCESS( f"{devicetype.model} - {hw_count} active devices" ))

                if hw_count > 0:
                    devices = Device.objects.filter(device_type=devicetype)
                    for device in devices:
                        if not device.serial:
                            self.stdout.write(self.style.WARNING( f'Found device "{device.name}" WITHOUT serial number - SKIPPING' ))
                            continue
                        
                        self.stdout.write(self.style.SUCCESS( f'Found device "{device.name} [{device.id}]" with serial number "{device.serial}"' ))
                        results[device.serial] = device.id

        except Device.DoesNotExist:
            raise CommandError( f'Manufacturer "{manufacturer_results}" has no Devices' )

        return results
    

    # Main entry point for the sync_cisco_hw_eox_data command of manage.py
    def handle(self, *args, **kwargs):
        MANUFACTURER = "Cisco"
        record_count = 0

        # Logon one time and gather the required API key
        api_call_headers = self.api_logon()

        # Step 1: Get all PIDs for all Device Types of that particular manufacturer
        device_serials = self.get_device_serials(MANUFACTURER)
        self.stdout.write(self.style.SUCCESS( f'Querying API for these Serial Numbers: ' + ', '.join(device_serials)))

        for serial, hw_id in device_serials.items():
            # Token Timer Check - Token expires after an Hour - Renew after 45 mins
            current_time = time.perf_counter()
            if (current_time - self.TOKEN_TIMER_START > 2700 ):
                print("Renewing Token")
                api_call_headers = self.api_logon()

            url = f'https://apix.cisco.com/cs/api/v1/contracts/coverage?customerId={self.CISCO_SERVICES_API_CUSTOMER_ID}&serialNumber={serial}'
            api_call_response = requests.get(url, headers=api_call_headers)
            self.stdout.write(self.style.SUCCESS('#######################################################'))
            self.stdout.write(self.style.SUCCESS(f'Calling {url}'))
            # sanatize file name
            filename = django.utils.text.get_valid_filename( f'{serial}.json' )

            # debug API answer to text file
            with open('/opt/netbox_lifecycle_cisco_api_results/%s' % filename, 'w') as outfile:
                outfile.write(api_call_response.text)

            # Validate response from Cisco 
            if api_call_response.status_code == 200:

                self.stdout.write(self.style.SUCCESS('API Success:'))
                # Deserialize JSON API Response into Python object "data"
                data = json.loads(api_call_response.text)
                pprint.pp(data)
                self.process_api_response(serial, data)

                # Call our Device Type Update method for that particular PID
                # self.update_lifecycle_data(pid, hw_type, data)

            else:

                # Show an error
                self.stdout.write(self.style.ERROR('API Error: ' + api_call_response.text))
            record_count = record_count + 1
            if (record_count == 10):
                exit()
