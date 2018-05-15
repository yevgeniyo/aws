import boto3
from ConfigParser import ConfigParser

# Parser for conf.ini where stored all configuration
config = ConfigParser()
config.read('config.ini')

account = config.get("main", "account")
source_region = config.get("main", "source_region")
dest_region = config.get("main", "dest_region")
dest_vpc_id = config.get("main", "dest_vpc_id")
source_sg_id = config.get("main", "source_sg_id")

sourceSG_details = {}
new_sg_id = ''


# Get vpcid, groupname, description from source SG
def get_sourceSG_details():
    boto3.setup_default_session(profile_name=account)
    ec2 = boto3.client('ec2', source_region)
    all_SG = ec2.describe_security_groups()['SecurityGroups']
    for group in all_SG:
        if group['GroupId'] == source_sg_id:
            sourceSG_details['vpcid'] = group['VpcId']
            sourceSG_details['groupname'] = group['GroupName']
            sourceSG_details['description'] = group['Description']
        else:
            pass
    return sourceSG_details


# Get all rules from source SG and add them to dict
def get_sourceSG_rules():
    boto3.setup_default_session(profile_name=account)
    ec2 = boto3.client('ec2', source_region)
    all_SG = ec2.describe_security_groups()['SecurityGroups']
    for group in all_SG:
        if group['GroupId'] == source_sg_id:
            rules = group['IpPermissions']
    return rules


# Create new security group in dest region
def create_new_sg():
    boto3.setup_default_session(profile_name=account)
    ec2 = boto3.client('ec2', dest_region)
    try:
        response = ec2.create_security_group(GroupName=sourceSG_details['groupname'],
                                             Description="{0} (copied from {1} automatically)".format(
                                                 sourceSG_details['description'], source_region),
                                             VpcId=dest_vpc_id)
        global new_sg_id
        new_sg_id = response['GroupId']

    except Exception as e:
        print(e)


# Add rules to new added SG
def add_rule_to_new_SG():
    boto3.setup_default_session(profile_name=account)
    ec2 = boto3.client('ec2', dest_region)
    try:
        for rule in get_sourceSG_rules():
            if rule['IpProtocol'] == '-1':
                for ip in rule['IpRanges']:
                    try:
                        ec2.authorize_security_group_ingress(GroupId=new_sg_id, IpProtocol='-1', CidrIp=ip['CidrIp'],
                                                             FromPort=0, ToPort=65535)
                    except Exception as e:
                        print(e)
            elif rule['IpProtocol'] == 'icmp':
                for ip in rule['IpRanges']:
                    try:
                        ec2.authorize_security_group_ingress(GroupId=new_sg_id, IpProtocol='icmp', CidrIp=ip['CidrIp'],
                                                             FromPort=-1, ToPort=-1)
                    except Exception as e:
                        print(e)
            else:
                for ip in rule['IpRanges']:
                    try:
                        ec2.authorize_security_group_ingress(GroupId=new_sg_id, IpProtocol=rule['IpProtocol'],
                                                             CidrIp=ip['CidrIp'],
                                                             FromPort=rule['FromPort'], ToPort=rule['ToPort'])
                    except Exception as e:
                        print(e)
    except Exception as e:
        print(e)


# Main
def main():
    get_sourceSG_details()
    create_new_sg()
    add_rule_to_new_SG()


if __name__ == '__main__':
    main()
