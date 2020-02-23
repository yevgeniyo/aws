__author__ = 'Yevgeniy Ovsyannikov'

import os
import time
import json
import xlrd
import click
import boto3
import logging
import datetime
import xlsxwriter
from kubernetes import client, config

# Common
date = datetime.datetime.now().strftime("%Y-%m-%d")
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(asctime)s - %(message)s',
                    datefmt='%m/%d/%Y %I:%M:%S %p')


# Class  fo reports flow
class GetReports(object):

    # Return all regions
    @staticmethod
    def get_all_regions():
        client = boto3.client("ec2")
        regions = [region['RegionName'] for region in client.describe_regions()['Regions']]
        return regions

    # Get filtered data about all EC2 instances from AWS pricing
    @staticmethod
    def get_ec2_prices_common():
        pricing_client = boto3.client('pricing', region_name='us-east-1')
        paginator = pricing_client.get_paginator('get_products')

        response_iterator = paginator.paginate(
            ServiceCode="AmazonEC2",
            Filters=[
                {
                    'Type': 'TERM_MATCH',
                    'Field': 'operatingSystem',
                    'Value': 'Linux'
                },
                {
                    'Type': 'TERM_MATCH',
                    'Field': 'location',
                    'Value': 'US East (N. Virginia)'
                },
                {
                    'Type': 'TERM_MATCH',
                    'Field': 'tenancy',
                    'Value': 'Shared'
                },
                {
                    'Type': 'TERM_MATCH',
                    'Field': 'preInstalledSw',
                    'Value': 'NA'
                },
                {
                    'Type': 'TERM_MATCH',
                    'Field': 'capacitystatus',
                    'Value': 'Used'
                }
            ],
            PaginationConfig={
                'PageSize': 100
            }
        )

        products = []
        instance_price = 0

        for response in response_iterator:
            for priceItem in response["PriceList"]:
                price_item_json = json.loads(priceItem)
                instance_type = price_item_json['product']['attributes']['instanceType']
                memory = price_item_json['product']['attributes']['memory']
                vcpu = price_item_json['product']['attributes']['vcpu']
                for key in price_item_json['terms']['OnDemand']:
                    for key1 in price_item_json['terms']['OnDemand'][key]['priceDimensions']:
                        instance_price = \
                            price_item_json['terms']['OnDemand'][key]['priceDimensions'][key1]['pricePerUnit'][
                                'USD']

                products.append({'instance_type': instance_type,
                                 'instance_price': instance_price,
                                 'memory': memory,
                                 'vcpu': vcpu})

        return products

    # Get filtered data about all EBS from AWS pricing
    @staticmethod
    def get_ebs_prices_common():
        pricing_client = boto3.client('pricing', region_name='us-east-1')
        paginator = pricing_client.get_paginator('get_products')
        response_iterator = paginator.paginate(
            ServiceCode="AmazonEC2",
            Filters=[
                {
                    'Type': 'TERM_MATCH',
                    'Field': 'productFamily',
                    'Value': 'Storage'
                },
                {
                    'Type': 'TERM_MATCH',
                    'Field': 'location',
                    'Value': 'US East (N. Virginia)'
                }
            ]
        )
        products = []
        volume_price = 0

        for response in response_iterator:
            for priceItem in response["PriceList"]:
                price_item_json = json.loads(priceItem)
                volume_type = price_item_json['product']['attributes']['volumeApiName']
                for key in price_item_json['terms']['OnDemand']:
                    for key1 in price_item_json['terms']['OnDemand'][key]['priceDimensions']:
                        volume_price = \
                            price_item_json['terms']['OnDemand'][key]['priceDimensions'][key1]['pricePerUnit']['USD']

                products.append({'volume_type': volume_type,
                                 'volume_price': round(float(volume_price), 5)})

        return products

    # Get all existing volumes with price calculation
    def all_existing_volumes(self):
        ebs_prices = self.get_ebs_prices_common()
        res = []
        volume_price = 0
        all_regions = self.get_all_regions()
        for region in all_regions:
            ec2 = boto3.resource('ec2', region)
            all_volumes = ec2.volumes.all()
            for volume in all_volumes:
                for item in ebs_prices:
                    if item['volume_type'] == volume.volume_type:
                        if item['volume_type'] == 'io1':
                            volume_price = (item['volume_price'] * volume.size) + (0.065 * volume.iops)
                        else:
                            volume_price = (item['volume_price'] * volume.size)
                res.append({'volume_id': volume.id, 'volume_iops': volume.iops, 'volume_size': volume.size,
                            'volume_type': volume.volume_type, 'volume_price_per_month': volume_price})
        return res

    # Return instances from all regions
    def get_all_instances(self):
        ec2_prices = self.get_ec2_prices_common()
        all_volumes = self.all_existing_volumes()
        res = []
        all_regions = self.get_all_regions()
        for region in all_regions:
            ec2_client = boto3.client("ec2", region)
            for group in ec2_client.describe_instances()['Reservations']:
                for instance in group['Instances']:
                    volumes_price = 0
                    instance_price = 0

                    # Calculating instance price
                    for item in ec2_prices:
                        if item['instance_type'].encode('utf-8') == instance['InstanceType'].encode('utf-8'):
                            instance_price = round(float(item['instance_price']) * float(24) * float(30.5), 2)

                    # Calculating volume price
                    block_devices_details = []
                    for attached_disk in instance['BlockDeviceMappings']:
                        attached_disk_id = attached_disk['Ebs']['VolumeId']
                        for disk in all_volumes:
                            if attached_disk_id == disk['volume_id']:
                                volumes_price += disk['volume_price_per_month']
                                block_devices_details.append(disk)

                    summary = volumes_price + instance_price

                    res.append({
                        'Tags': instance['Tags'],
                        'InstanceId': instance['InstanceId'],
                        'PublicIp': instance['PublicIpAddress'] if 'PublicIpAddress' in instance else '',
                        'PrivateIp': instance['PrivateIpAddress'] if 'PrivateIpAddress' in instance else '',
                        'State': instance['State']['Name'],
                        'InstanceType': instance['InstanceType'],
                        'Region': region,
                        'LaunchTime': instance['LaunchTime'],
                        # 'BlockDevices': [device['Ebs']['VolumeId'] for device in instance['BlockDeviceMappings']],
                        'BlockDevicesDetails': block_devices_details,
                        'Price': {'instance_price_per_month': instance_price,
                                  'volumes_price_per_month': round(volumes_price, 4),
                                  'summary': round(summary, 4)
                                  }
                    })

        return res

    # Return instances for specific department
    def get_instances_per_department(self, department):
        all_instances = self.get_all_instances()
        res = []
        for instance in all_instances:
            for tag in instance['Tags']:
                if tag['Key'] == 'Department':
                    if tag['Value'] == department:
                        res.append(instance)
        return res

    # Creating excel
    def get_report_excel(self, department):
        account_id = boto3.client('sts').get_caller_identity().get('Account')
        if not os.path.exists('reports'):
            os.makedirs('reports')

        logging.info('Gathering data for depratment - {}, it will not take more than a minute'.format(department))
        if department != 'common':
            all_instances = self.get_instances_per_department(department)
            if len(all_instances) == 0:
                logging.info('No instances found for department {}'.format(department))
                exit(0)
        else:
            all_instances = self.get_all_instances()

        nested_department = department.replace(" ", "")
        workbook = xlsxwriter.Workbook(
            'reports/AWS-report-{0}-{1}-({2}).xlsx'.format(nested_department, account_id, date))
        worksheet = workbook.add_worksheet()
        head_red = workbook.add_format({'bold': True, 'font_color': 'red', 'font_size': 16, 'bg_color': '#D8D9DC'})
        head_brown = workbook.add_format({'bold': True, 'font_color': 'brown', 'font_size': 16, 'bg_color': '#D8D9DC'})
        head_green = workbook.add_format({'bold': True, 'font_color': 'green', 'font_size': 16, 'bg_color': '#D8D9DC'})
        alignment = workbook.add_format({'align': 'left'})

        worksheet.autofilter('A1:P1')

        worksheet.set_column('A:A', 10)
        worksheet.set_column('B:B', 40)
        worksheet.set_column('C:C', 20)
        worksheet.set_column('D:D', 15)
        worksheet.set_column('E:E', 10)
        worksheet.set_column('F:F', 15)
        worksheet.set_column('G:G', 20)
        worksheet.set_column('H:H', 40)
        worksheet.set_column('I:I', 20)
        worksheet.set_column('J:J', 20)
        worksheet.set_column('K:K', 20)
        worksheet.set_column('L:L', 10)
        worksheet.set_column('M:M', 20)
        worksheet.set_column('N:N', 40)
        worksheet.set_column('O:O', 40)
        worksheet.set_column('P:P', 50)
        worksheet.set_column('Q:Q', 30)

        worksheet.write('A1', 'Region', head_red)
        worksheet.write('B1', 'Instance name', head_red)
        worksheet.write('C1', 'ID', head_red)
        worksheet.write('D1', 'Type', head_red)
        worksheet.write('E1', 'State', head_red)
        worksheet.write('F1', 'Public IP', head_red)
        worksheet.write('G1', 'Launch time', head_red)
        worksheet.write('H1', 'Department', head_brown)
        worksheet.write('I1', 'Team', head_brown)
        worksheet.write('J1', 'Team Owner', head_brown)
        worksheet.write('K1', 'Project', head_brown)
        worksheet.write('L1', 'Finance', head_brown)
        worksheet.write('M1', 'Environment', head_brown)
        worksheet.write('N1', 'Compute monthly cost (USD)', head_green)
        worksheet.write('O1', 'Storage monthly cost (USD)', head_green)
        worksheet.write('P1', 'Compute + Storage monthly cost (USD)', head_green)
        worksheet.write('Q1', 'Block devices size (GB)', head_red)

        line = 2

        logging.info('Building excel...')
        for instance in all_instances:
            worksheet.write('A{}'.format(line), instance['Region'], alignment)
            worksheet.write('B{}'.format(line),
                            [tag['Value'] for tag in instance['Tags'] if tag['Key'] == 'Name'][0] if
                            len([tag['Value'] for tag in instance['Tags'] if tag['Key'] == 'Name']) != 0 else '',
                            alignment)
            worksheet.write('C{}'.format(line), instance['InstanceId'], alignment)
            worksheet.write('D{}'.format(line), instance['InstanceType'], alignment)
            worksheet.write('E{}'.format(line), instance['State'], alignment)
            worksheet.write('F{}'.format(line), instance['PublicIp'], alignment)
            worksheet.write('G{}'.format(line), str(instance['LaunchTime'])[0:-6], alignment)
            worksheet.write('H{}'.format(line),
                            [tag['Value'] for tag in instance['Tags'] if tag['Key'] == 'Department'][0]
                            if len(
                                [tag['Value'] for tag in instance['Tags'] if tag['Key'] == 'Department']) != 0 else '',
                            alignment)
            worksheet.write('I{}'.format(line),
                            [tag['Value'] for tag in instance['Tags'] if tag['Key'] == 'Team'][0]
                            if len([tag['Value'] for tag in instance['Tags'] if tag['Key'] == 'Team']) != 0 else '',
                            alignment)
            worksheet.write('J{}'.format(line),
                            [tag['Value'] for tag in instance['Tags'] if tag['Key'] == 'TeamOwner'][0]
                            if len(
                                [tag['Value'] for tag in instance['Tags'] if tag['Key'] == 'TeamOwner']) != 0 else '',
                            alignment)
            worksheet.write('K{}'.format(line),
                            [tag['Value'] for tag in instance['Tags'] if tag['Key'] == 'Project'][0]
                            if len([tag['Value'] for tag in instance['Tags'] if tag['Key'] == 'Project']) != 0 else '',
                            alignment)
            worksheet.write('L{}'.format(line),
                            [tag['Value'] for tag in instance['Tags'] if tag['Key'] == 'Finance'][0]
                            if len([tag['Value'] for tag in instance['Tags'] if tag['Key'] == 'Finance']) != 0 else '',
                            alignment)
            worksheet.write('M{}'.format(line),
                            [tag['Value'] for tag in instance['Tags'] if tag['Key'] == 'Environment'][0]
                            if len(
                                [tag['Value'] for tag in instance['Tags'] if tag['Key'] == 'Environment']) != 0 else '',
                            alignment)
            worksheet.write('N{}'.format(line), instance['Price']['instance_price_per_month'], alignment)
            worksheet.write('O{}'.format(line), instance['Price']['volumes_price_per_month'], alignment)
            worksheet.write('P{}'.format(line), instance['Price']['summary'], alignment)
            worksheet.write('Q{}'.format(line),
                            str([volume['volume_size'] for volume in instance['BlockDevicesDetails']]),
                            alignment)

            line += 1

        workbook.close()


# Class for update_tags flow
class UpdateTags(object):

    def __init__(self, filename):
        self.filename = filename
        try:
            book = xlrd.open_workbook(self.filename)
            self.first_sheet = book.sheet_by_index(0)
        except Exception as exception:
            logging.info(exception)
            exit(1)

    # Parsing excel
    def _parse_excel(self, line_number, column_number):
        instance_id = self.first_sheet.cell(line_number, 2)
        tag_value = self.first_sheet.cell(line_number, column_number)
        result = {'instance-id': instance_id, 'tag': tag_value}
        return result

    # Adding updated tags to EC2
    @staticmethod
    def _add_tag_to_ec2(region, instance_id, tag_department, tag_team, tag_team_owner, tag_project, tag_finance,
                        tag_environment):
        try:
            ec2 = boto3.client('ec2', region)
            ec2.create_tags(Resources=[instance_id], Tags=[{'Key': 'Department', 'Value': tag_department},
                                                           {'Key': 'TeamOwner', 'Value': tag_team_owner},
                                                           {'Key': 'Project', 'Value': tag_project},
                                                           {'Key': 'Finance', 'Value': tag_finance},
                                                           {'Key': 'Team', 'Value': tag_team},
                                                           {'Key': 'Environment', 'Value': tag_environment},
                                                           ])
        except Exception as exception:
            logging.info(exception)
            exit(1)

    # Update tags from provided file
    def update_tags(self):
        number_of_lines = int(self.first_sheet.nrows)

        for line_number in range(1, number_of_lines):
            region = self._parse_excel(line_number, 0)['tag'].value
            instance_id = self._parse_excel(line_number, 2)['instance-id'].value
            department = self._parse_excel(line_number, 7)['tag'].value
            team = self._parse_excel(line_number, 8)['tag'].value
            team_owner = self._parse_excel(line_number, 9)['tag'].value
            project = self._parse_excel(line_number, 10)['tag'].value
            finance = self._parse_excel(line_number, 11)['tag'].value
            environment = self._parse_excel(line_number, 12)['tag'].value

            logging.info('Modifying: {0} with new tags: {1}, {2}, {3}, {4}, {5}, {6}'.format(instance_id, department,
                                                                                             team, team_owner, project,
                                                                                             finance, environment))
            try:
                self._add_tag_to_ec2(region, instance_id, department, team, team_owner, project, finance, environment)
            except Exception as exception:
                logging.info(exception)
                exit(1)


# Class for kubernetes reports flow
class GetReportKubernetes(object):
    def __init__(self):
        config.load_kube_config()
        self.v1 = client.CoreV1Api()

    @staticmethod
    def cluster_name():
        return config.list_kube_config_contexts()[1]['name'].split(".")[0]

    def get_all_namespaces(self):
        ret = self.v1.list_namespace()
        namespaces = []
        for namespace in ret.items:
            namespaces.append(namespace.metadata.name)
        return namespaces

    def get_pod_resources(self, namespace, pod_name):
        ret = self.v1.read_namespaced_pod(namespace=namespace, name=pod_name)
        return ret.spec.containers[0].resources.requests

    # Price per CPU
    @staticmethod
    def get_price_per_cpu(cpu):
        one_cpu_price = 63  # price for 1 vCPU RAM in $
        cpu = cpu / 1000
        price = cpu * one_cpu_price
        return round(price, 1)

    # Price per RAM
    @staticmethod
    def get_price_per_ram(ram):
        one_gb_ram_price = 16  # price for 1Gb RAM in $
        ram = ram / 1000
        price = ram * one_gb_ram_price
        return round(price, 1)

    def structured_data(self):
        result = []
        for namespace in self.get_all_namespaces():

            endpoints = self.v1.list_namespaced_endpoints(watch=False, namespace=namespace)
            for service in endpoints.items:
                if 'headless' in service.metadata.name:
                    continue
                labels = service.metadata.labels
                if labels is not None and 'owner' in labels:
                    owner_label = labels['owner']
                else:
                    owner_label = 'Not set'
                if service.subsets is not None:
                    pod_names = []
                    for subset in service.subsets:
                        if subset.addresses is not None:
                            for pod in subset.addresses:
                                if pod.target_ref is not None:
                                    pod_names.append(pod.target_ref.name)
                    # pod_resources = None
                    if len(pod_names) != 0:
                        pod_resources = self.get_pod_resources(namespace=namespace, pod_name=pod_names[0])
                        if pod_resources is not None:
                            result.append([{'service-name': service.metadata.name},
                                           {'pods': pod_names},
                                           {'namespace': service.metadata.namespace},
                                           {'one_pod_resource': pod_resources},
                                           {'owner': owner_label}
                                           ])
        return result

    # Pretty CPU:
    @staticmethod
    def pretty_cpu(cpu):
        if 'm' in cpu:
            cpu = cpu[0:-1]
        else:
            cpu = cpu + '000'

        return int(cpu)

    # Pretty RAM
    @staticmethod
    def pretty_ram(ram):
        if 'Mi' in ram:
            ram = ram[0:-2]
        if 'Gi' in ram:
            ram = ram[0:-2] + '000'

        return int(ram)

    # Creating excel
    def get_report_excel(self):
        if not os.path.exists('reports'):
            os.makedirs('reports')

        workbook = xlsxwriter.Workbook(
            'reports/kube-report-({0})-({1}).xlsx'.format(self.cluster_name(), date))
        worksheet = workbook.add_worksheet()
        head_red = workbook.add_format({'bold': True, 'font_color': 'red', 'font_size': 16, 'bg_color': '#D8D9DC'})

        alignment = workbook.add_format({'align': 'left'})
        alignment_center = workbook.add_format({'align': 'center'})

        worksheet.autofilter('A1:J1')

        worksheet.set_column('A:A', 45)
        worksheet.set_column('B:B', 30)
        worksheet.set_column('C:C', 10)
        worksheet.set_column('D:D', 15)
        worksheet.set_column('E:E', 15)
        worksheet.set_column('F:F', 15)
        worksheet.set_column('G:G', 20)
        worksheet.set_column('H:H', 20)
        worksheet.set_column('I:I', 25)
        worksheet.set_column('J:J', 25)

        worksheet.write('A1', 'Service Name', head_red)
        worksheet.write('B1', 'Namespace', head_red)
        worksheet.write('C1', 'Owner', head_red)
        worksheet.write('D1', 'Number of pods', head_red)
        worksheet.write('E1', 'CPU (one pod)', head_red)
        worksheet.write('F1', 'RAM (one pod)', head_red)
        worksheet.write('G1', 'CPU (total)', head_red)
        worksheet.write('H1', 'RAM (total)', head_red)
        worksheet.write('I1', 'Price per CPU (USD)', head_red)
        worksheet.write('J1', 'Price per RAM (USD)', head_red)

        line = 2

        logging.info('Building excel...')
        all_data = self.structured_data()
        for instance in all_data:
            pod_number = len(instance[1]['pods'])
            cpu = self.pretty_cpu(instance[3]['one_pod_resource']['cpu'])
            ram = self.pretty_ram(instance[3]['one_pod_resource']['memory'])
            total_cpu = pod_number * cpu
            total_ram = pod_number * ram
            owner = instance[4]['owner']
            price_per_cpu = self.get_price_per_cpu(total_cpu)
            price_per_ram = self.get_price_per_ram(total_ram)

            worksheet.write('A{}'.format(line), instance[0]['service-name'], alignment)
            worksheet.write('B{}'.format(line), str(instance[2]['namespace']), alignment)
            worksheet.write('C{}'.format(line), owner, alignment_center)
            worksheet.write('D{}'.format(line), pod_number, alignment_center)
            worksheet.write('E{}'.format(line), cpu, alignment)
            worksheet.write('F{}'.format(line), ram, alignment)
            worksheet.write('G{}'.format(line), total_cpu, alignment)
            worksheet.write('H{}'.format(line), total_ram, alignment)
            worksheet.write('I{}'.format(line), price_per_cpu, alignment_center)
            worksheet.write('J{}'.format(line), price_per_ram, alignment_center)

            line += 1

        workbook.close()


# Main
@click.command()
@click.argument('profile')
@click.argument('flow')
@click.option(
    '-d', '--department', default='common',
    help='Provide name of department for generate_report, by default it will be common',
)
@click.option(
    '-f', '--filename',
    help='Provide name of file for parsing new tags',
)
def main(profile, flow, filename, department='common'):
    """

    A little AWS tool, which will help you organize your AWS EC2 environment with tags and generate
    reports about cost of your instances
    -------------------------------------------------------------------------
    PROFILE

        You need configure some profile under ~/.aws/credentials like

        [profile stage]\n
        aws_access_key_id = <key>\n
        aws_secret_access_key = <secret>

    FLOW

        Could be: report, update_tags, kube-report

    Examples:

    1. Will generate report for all EC2 instances for profile stage

        main.py stage report

    2. Will generate report for particular department for profile stage

        main.py stage report -d <interested you department>

    3. Generate report with previous commands, fill tags, update it this way for profile stage

        main.py stage update_tags -f reports/AWS-report-common-xxxxxxxxx-(xxxx-yy-zz).xlsx

    4. Get report for kubernetes

        main.py stage kube-report    

    Enjoy!

    """

    start_time = time.time()

    try:
        boto3.setup_default_session(profile_name=profile, region_name='us-east-1')
    except Exception as exception:
        logging.info(exception)
        logging.info('Check that profile {} exists under ~/.aws/credentials'.format(profile))
        exit(1)

    logging.info('Welcome to tag optimizer')
    logging.info('We are working for profile - {}'.format(profile))

    if flow == 'report':
        logging.info('Getting report from your profile, find it under reports/ folder')
        report = GetReports()
        report.get_report_excel(department)
    elif flow == 'update_tags':
        logging.info('Updating tags from file - {}'.format(filename))
        tags = UpdateTags(filename)
        tags.update_tags()
    elif flow == 'kube-report':
        logging.info('Generating kub-report')
        kub_report = GetReportKubernetes()
        kub_report.get_report_excel()
    else:
        logging.info('Not existing flow - {}, valid flows are: report, update_tags, kube-report'.format(flow))
        exit(1)

    logging.info('Done')
    logging.info('Whole process took {} seconds'.format(round(time.time() - start_time), 0))


if __name__ == '__main__':
    main()
