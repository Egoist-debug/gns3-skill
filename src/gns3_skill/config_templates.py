#!/usr/bin/env python3
"""
Configuration Templates for Common Network Scenarios
Pre-built configurations for routers, switches, and other devices.
"""

from typing import Dict, List, Optional


class ConfigTemplates:
    """Repository of configuration templates for network devices."""
    
    @staticmethod
    def basic_router_config(hostname: str, domain: str = "local") -> List[str]:
        """Basic router configuration with hostname and domain."""
        return [
            f"hostname {hostname}",
            f"ip domain-name {domain}",
            "no ip domain-lookup",
            "line con 0",
            "logging synchronous",
            "exec-timeout 0 0",
            "exit",
        ]
    
    @staticmethod
    def interface_config(interface: str, ip_address: str, subnet_mask: str,
                        description: Optional[str] = None) -> List[str]:
        """Configure an interface with IP address."""
        config = [f"interface {interface}"]
        if description:
            config.append(f"description {description}")
        config.extend([
            f"ip address {ip_address} {subnet_mask}",
            "no shutdown",
            "exit"
        ])
        return config
    
    @staticmethod
    def ospf_config(process_id: int, router_id: str, networks: List[Dict[str, str]]) -> List[str]:
        """Configure OSPF routing protocol."""
        config = [
            f"router ospf {process_id}",
            f"router-id {router_id}",
        ]
        for net in networks:
            config.append(f"network {net['network']} {net['wildcard']} area {net.get('area', 0)}")
        config.append("exit")
        return config
    
    @staticmethod
    def eigrp_config(as_number: int, networks: List[str], router_id: Optional[str] = None) -> List[str]:
        """Configure EIGRP routing protocol."""
        config = [f"router eigrp {as_number}"]
        if router_id:
            config.append(f"eigrp router-id {router_id}")
        for network in networks:
            config.append(f"network {network}")
        config.extend([
            "no auto-summary",
            "exit"
        ])
        return config
    
    @staticmethod
    def bgp_config(as_number: int, router_id: str, neighbors: List[Dict[str, str]]) -> List[str]:
        """Configure BGP routing protocol."""
        config = [
            f"router bgp {as_number}",
            f"bgp router-id {router_id}",
        ]
        for neighbor in neighbors:
            config.append(f"neighbor {neighbor['ip']} remote-as {neighbor['as']}")
            if neighbor.get('description'):
                config.append(f"neighbor {neighbor['ip']} description {neighbor['description']}")
        config.append("exit")
        return config
    
    @staticmethod
    def static_route(network: str, mask: str, next_hop: str, 
                    admin_distance: Optional[int] = None) -> List[str]:
        """Configure a static route."""
        route = f"ip route {network} {mask} {next_hop}"
        if admin_distance:
            route += f" {admin_distance}"
        return [route]
    
    @staticmethod
    def default_route(next_hop: str) -> List[str]:
        """Configure a default route."""
        return [f"ip route 0.0.0.0 0.0.0.0 {next_hop}"]
    
    @staticmethod
    def vlan_config(vlan_id: int, name: str) -> List[str]:
        """Create and name a VLAN."""
        return [
            f"vlan {vlan_id}",
            f"name {name}",
            "exit"
        ]
    
    @staticmethod
    def trunk_port_config(interface: str, allowed_vlans: Optional[str] = None) -> List[str]:
        """Configure a trunk port."""
        config = [
            f"interface {interface}",
            "switchport trunk encapsulation dot1q",
            "switchport mode trunk",
        ]
        if allowed_vlans:
            config.append(f"switchport trunk allowed vlan {allowed_vlans}")
        config.extend([
            "no shutdown",
            "exit"
        ])
        return config
    
    @staticmethod
    def access_port_config(interface: str, vlan: int, 
                          portfast: bool = True, bpduguard: bool = True) -> List[str]:
        """Configure an access port."""
        config = [
            f"interface {interface}",
            "switchport mode access",
            f"switchport access vlan {vlan}",
        ]
        if portfast:
            config.append("spanning-tree portfast")
        if bpduguard:
            config.append("spanning-tree bpduguard enable")
        config.extend([
            "no shutdown",
            "exit"
        ])
        return config
    
    @staticmethod
    def dhcp_pool_config(pool_name: str, network: str, mask: str,
                        default_router: str, dns_servers: Optional[List[str]] = None,
                        excluded_addresses: Optional[List[tuple]] = None) -> List[str]:
        """Configure DHCP pool."""
        config = []
        
        # Exclude addresses if specified
        if excluded_addresses:
            for start, end in excluded_addresses:
                config.append(f"ip dhcp excluded-address {start} {end}")
        
        # DHCP pool configuration
        config.extend([
            f"ip dhcp pool {pool_name}",
            f"network {network} {mask}",
            f"default-router {default_router}",
        ])
        
        if dns_servers:
            dns_list = " ".join(dns_servers)
            config.append(f"dns-server {dns_list}")
        
        config.append("exit")
        return config
    
    @staticmethod
    def nat_overload_config(inside_interfaces: List[str], outside_interface: str,
                           acl_number: int, allowed_networks: List[str]) -> List[str]:
        """Configure NAT overload (PAT)."""
        config = []
        
        # Configure inside interfaces
        for interface in inside_interfaces:
            config.extend([
                f"interface {interface}",
                "ip nat inside",
                "exit"
            ])
        
        # Configure outside interface
        config.extend([
            f"interface {outside_interface}",
            "ip nat outside",
            "exit"
        ])
        
        # Create ACL
        config.append(f"access-list {acl_number} remark NAT ACL")
        for network in allowed_networks:
            config.append(f"access-list {acl_number} permit {network}")
        
        # NAT overload command
        config.append(f"ip nat inside source list {acl_number} interface {outside_interface} overload")
        
        return config
    
    @staticmethod
    def standard_acl(acl_number: int, entries: List[Dict[str, str]]) -> List[str]:
        """Configure standard ACL."""
        config = [f"access-list {acl_number} remark Standard ACL"]
        for entry in entries:
            action = entry.get('action', 'permit')
            source = entry.get('source', 'any')
            config.append(f"access-list {acl_number} {action} {source}")
        return config
    
    @staticmethod
    def extended_acl(acl_number: int, entries: List[Dict[str, str]]) -> List[str]:
        """Configure extended ACL."""
        config = [f"access-list {acl_number} remark Extended ACL"]
        for entry in entries:
            action = entry.get('action', 'permit')
            protocol = entry.get('protocol', 'ip')
            source = entry.get('source', 'any')
            destination = entry.get('destination', 'any')
            
            line = f"access-list {acl_number} {action} {protocol} {source} {destination}"
            
            # Add port information if present
            if 'port' in entry:
                line += f" eq {entry['port']}"
            
            config.append(line)
        return config
    
    @staticmethod
    def ssh_config(domain: str, username: str, password: str,
                  crypto_key_size: int = 1024, vty_lines: str = "0 4") -> List[str]:
        """Configure SSH access."""
        return [
            f"ip domain-name {domain}",
            f"crypto key generate rsa modulus {crypto_key_size}",
            "ip ssh version 2",
            f"username {username} privilege 15 secret {password}",
            f"line vty {vty_lines}",
            "transport input ssh",
            "login local",
            "exit"
        ]
    
    @staticmethod
    def banner_config(banner_type: str, message: str) -> List[str]:
        """Configure login or MOTD banner."""
        return [
            f"banner {banner_type} ^",
            message,
            "^"
        ]
    
    @staticmethod
    def ntp_config(ntp_servers: List[str]) -> List[str]:
        """Configure NTP."""
        config = []
        for server in ntp_servers:
            config.append(f"ntp server {server}")
        return config
    
    @staticmethod
    def logging_config(syslog_server: str, trap_level: str = "informational") -> List[str]:
        """Configure logging to syslog server."""
        return [
            f"logging host {syslog_server}",
            f"logging trap {trap_level}",
            "logging on"
        ]
    
    @staticmethod
    def snmp_config(community: str, access: str = "ro", acl: Optional[int] = None) -> List[str]:
        """Configure SNMP."""
        cmd = f"snmp-server community {community} {access}"
        if acl:
            cmd += f" {acl}"
        return [cmd]
    
    @staticmethod
    def vpcs_basic_config(ip_address: str, subnet_mask: str, gateway: str) -> str:
        """VPCS configuration command."""
        # Convert subnet mask to CIDR
        cidr = sum([bin(int(x)).count('1') for x in subnet_mask.split('.')])
        return f"ip {ip_address}/{cidr} {gateway}"
    
    @staticmethod
    def vpcs_dhcp_config() -> str:
        """VPCS DHCP client configuration."""
        return "ip dhcp"
    
    @staticmethod
    def security_hardening_basic() -> List[str]:
        """Basic security hardening commands."""
        return [
            "no ip http server",
            "no ip http secure-server",
            "service password-encryption",
            "security passwords min-length 8",
            "login block-for 120 attempts 3 within 60",
            "no cdp run",
            "no ip source-route",
            "no ip proxy-arp",
            "ip tcp synwait-time 10",
        ]
    
    @staticmethod
    def qos_basic_marking(class_name: str, dscp_value: int, interfaces: List[str]) -> List[str]:
        """Basic QoS marking configuration."""
        config = [
            f"class-map match-all {class_name}",
            "match access-group name QOS_ACL",
            "exit",
            f"policy-map QOS_POLICY",
            f"class {class_name}",
            f"set dscp {dscp_value}",
            "exit",
            "exit",
        ]
        
        for interface in interfaces:
            config.extend([
                f"interface {interface}",
                "service-policy output QOS_POLICY",
                "exit"
            ])
        
        return config


class TopologyTemplates:
    """Pre-defined topology templates."""
    
    @staticmethod
    def simple_lan() -> Dict[str, any]:
        """Simple LAN topology: Router + Switch + 2 PCs."""
        return {
            "name": "Simple LAN",
            "description": "Basic LAN with router, switch, and 2 PCs",
            "devices": [
                {"name": "R1", "type": "router", "x": 0, "y": -100},
                {"name": "SW1", "type": "switch", "x": 0, "y": 0},
                {"name": "PC1", "type": "vpcs", "x": -100, "y": 100},
                {"name": "PC2", "type": "vpcs", "x": 100, "y": 100},
            ],
            "links": [
                {"from": "R1", "from_port": 0, "to": "SW1", "to_port": 0},
                {"from": "SW1", "from_port": 1, "to": "PC1", "to_port": 0},
                {"from": "SW1", "from_port": 2, "to": "PC2", "to_port": 0},
            ]
        }
    
    @staticmethod
    def dual_router_topology() -> Dict[str, any]:
        """Two routers connected with PCs on each side."""
        return {
            "name": "Dual Router",
            "description": "Two routers interconnected with PCs on each side",
            "devices": [
                {"name": "R1", "type": "router", "x": -200, "y": 0},
                {"name": "R2", "type": "router", "x": 200, "y": 0},
                {"name": "SW1", "type": "switch", "x": -200, "y": 100},
                {"name": "SW2", "type": "switch", "x": 200, "y": 100},
                {"name": "PC1", "type": "vpcs", "x": -250, "y": 200},
                {"name": "PC2", "type": "vpcs", "x": -150, "y": 200},
                {"name": "PC3", "type": "vpcs", "x": 150, "y": 200},
                {"name": "PC4", "type": "vpcs", "x": 250, "y": 200},
            ],
            "links": [
                {"from": "R1", "from_port": 0, "to": "R2", "to_port": 0},
                {"from": "R1", "from_port": 1, "to": "SW1", "to_port": 0},
                {"from": "R2", "from_port": 1, "to": "SW2", "to_port": 0},
                {"from": "SW1", "from_port": 1, "to": "PC1", "to_port": 0},
                {"from": "SW1", "from_port": 2, "to": "PC2", "to_port": 0},
                {"from": "SW2", "from_port": 1, "to": "PC3", "to_port": 0},
                {"from": "SW2", "from_port": 2, "to": "PC4", "to_port": 0},
            ]
        }
    
    @staticmethod
    def hub_and_spoke() -> Dict[str, any]:
        """Hub and spoke topology with central router."""
        return {
            "name": "Hub and Spoke",
            "description": "Central hub router with three spoke routers",
            "devices": [
                {"name": "HUB", "type": "router", "x": 0, "y": 0},
                {"name": "SPOKE1", "type": "router", "x": -200, "y": -200},
                {"name": "SPOKE2", "type": "router", "x": 200, "y": -200},
                {"name": "SPOKE3", "type": "router", "x": 0, "y": 200},
            ],
            "links": [
                {"from": "HUB", "from_port": 0, "to": "SPOKE1", "to_port": 0},
                {"from": "HUB", "from_port": 1, "to": "SPOKE2", "to_port": 0},
                {"from": "HUB", "from_port": 2, "to": "SPOKE3", "to_port": 0},
            ]
        }
