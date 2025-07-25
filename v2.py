import random
import logging
import subprocess
import sys
import os
import re
import time
import concurrent.futures
import discord
from discord.ext import commands, tasks
import docker
import asyncio
from discord import app_commands
from discord.ui import Button, View, Select
import string
from datetime import datetime, timedelta
from typing import Optional, Literal

TOKEN = ''
RAM_LIMIT = '64g'
SERVER_LIMIT = 1
database_file = 'database.txt'
PUBLIC_IP = '138.68.79.95'

# Admin user IDs - add your admin user IDs here
ADMIN_IDS = [1258646055860568094,1159037240622723092]  # Replace with actual admin IDs

intents = discord.Intents.default()
intents.messages = False
intents.message_content = False

bot = commands.Bot(command_prefix='/', intents=intents)
client = docker.from_env()

# Helper functions
def is_admin(user_id):
    return user_id in ADMIN_IDS

def generate_random_string(length=8):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def generate_random_port(): 
    return random.randint(1025, 65535)

def parse_time_to_seconds(time_str):
    """Convert time string like '1d', '2h', '30m', '45s', '1y', '3M' to seconds"""
    if not time_str:
        return None
    
    units = {
        's': 1,               # seconds
        'm': 60,              # minutes
        'h': 3600,            # hours
        'd': 86400,           # days
        'M': 2592000,         # months (30 days)
        'y': 31536000         # years (365 days)
    }
    
    unit = time_str[-1]
    if unit in units and time_str[:-1].isdigit():
        return int(time_str[:-1]) * units[unit]
    elif time_str.isdigit():
        return int(time_str) * 86400  # Default to days if no unit specified
    return None

def format_expiry_date(seconds_from_now):
    """Convert seconds from now to a formatted date string"""
    if not seconds_from_now:
        return None
    
    expiry_date = datetime.now() + timedelta(seconds=seconds_from_now)
    return expiry_date.strftime("%Y-%m-%d %H:%M:%S")

def add_to_database(user, container_name, ssh_command, ram_limit=None, cpu_limit=None, creator=None, expiry=None, os_type="Ubuntu 22.04"):
    with open(database_file, 'a') as f:
        f.write(f"{user}|{container_name}|{ssh_command}|{ram_limit or '2048'}|{cpu_limit or '1'}|{creator or user}|{os_type}|{expiry or 'None'}\n")

def remove_from_database(container_id):
    if not os.path.exists(database_file):
        return
    with open(database_file, 'r') as f:
        lines = f.readlines()
    with open(database_file, 'w') as f:
        for line in lines:
            if container_id not in line:
                f.write(line)

def get_all_containers():
    if not os.path.exists(database_file):
        return []
    with open(database_file, 'r') as f:
        return [line.strip() for line in f.readlines()]

def get_container_stats(container_id):
    try:
        # Get memory usage
        mem_stats = subprocess.check_output(["docker", "stats", container_id, "--no-stream", "--format", "{{.MemUsage}}"]).decode().strip()
        
        # Get CPU usage
        cpu_stats = subprocess.check_output(["docker", "stats", container_id, "--no-stream", "--format", "{{.CPUPerc}}"]).decode().strip()
        
        # Get container status
        status = subprocess.check_output(["docker", "inspect", "--format", "{{.State.Status}}", container_id]).decode().strip()
        
        return {
            "memory": mem_stats,
            "cpu": cpu_stats,
            "status": "🟢 Running" if status == "running" else "🔴 Stopped"
        }
    except Exception:
        return {"memory": "N/A", "cpu": "N/A", "status": "🔴 Stopped"}

def get_system_stats():
    try:
        # Get total memory usage
        total_mem = subprocess.check_output(["free", "-m"]).decode().strip()
        mem_lines = total_mem.split('\n')
        if len(mem_lines) >= 2:
            mem_values = mem_lines[1].split()
            total_mem = mem_values[1]
            used_mem = mem_values[2]
            
        # Get disk usage
        disk_usage = subprocess.check_output(["df", "-h", "/"]).decode().strip()
        disk_lines = disk_usage.split('\n')
        if len(disk_lines) >= 2:
            disk_values = disk_lines[1].split()
            total_disk = disk_values[1]
            used_disk = disk_values[2]
            
        return {
            "total_memory": f"{total_mem}GB",
            "used_memory": f"{used_mem}GB",
            "total_disk": total_disk,
            "used_disk": used_disk
        }
    except Exception as e:
        return {
            "total_memory": "N/A",
            "used_memory": "N/A",
            "total_disk": "N/A",
            "used_disk": "N/A",
            "error": str(e)
        }

async def capture_ssh_session_line(process):
    while True:
        output = await process.stdout.readline()
        if not output:
            break
        output = output.decode('utf-8').strip()
        if "ssh session:" in output:
            return output.split("ssh session:")[1].strip()
    return None

def get_ssh_command_from_database(container_id):
    if not os.path.exists(database_file):
        return None
    with open(database_file, 'r') as f:
        for line in f:
            if container_id in line:
                parts = line.strip().split('|')
                if len(parts) >= 3:
                    return parts[2]
    return None

def get_user_servers(user):
    if not os.path.exists(database_file):
        return []
    servers = []
    with open(database_file, 'r') as f:
        for line in f:
            if line.startswith(user):
                servers.append(line.strip())
    return servers

def count_user_servers(user):
    return len(get_user_servers(user))

def get_container_id_from_database(user, container_name=None):
    servers = get_user_servers(user)
    if servers:
        if container_name:
            for server in servers:
                parts = server.split('|')
                if len(parts) >= 2 and container_name in parts[1]:
                    return parts[1]
            return None
        else:
            return servers[0].split('|')[1]
    return None

# OS Selection dropdown for deploy command
# OS Selection dropdown for deploy command
class OSSelectView(View):
    def __init__(self, callback):
        super().__init__(timeout=60)
        self.callback = callback
        
        # Create the OS selection dropdown
        select = Select(
            placeholder="Select an operating system",
            options=[
                discord.SelectOption(label="Ubuntu 22.04", description="Latest LTS Ubuntu release", emoji="🐧", value="ubuntu"),
                discord.SelectOption(label="Debian 12", description="Stable Debian release", emoji="🐧", value="debian")
            ]
        )
        
        select.callback = self.select_callback
        self.add_item(select)
        
    async def select_callback(self, interaction: discord.Interaction):
        selected_os = interaction.data["values"][0]
        await interaction.response.defer()
        await self.callback(interaction, selected_os)

# Confirmation dialog class for delete operations
# Confirmation dialog class for delete operations
class ConfirmView(View):
    def __init__(self, container_id, container_name, is_delete_all=False):
        super().__init__(timeout=60)
        self.container_id = container_id
        self.container_name = container_name
        self.is_delete_all = is_delete_all
        
    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # First, acknowledge the interaction to prevent timeout
        await interaction.response.defer(ephemeral=False)
        
        try:
            if self.is_delete_all:
                # Delete all VPS instances
                containers = get_all_containers()
                deleted_count = 0
                
                for container_info in containers:
                    parts = container_info.split('|')
                    if len(parts) >= 2:
                        container_id = parts[1]
                        try:
                            subprocess.run(["docker", "stop", container_id], check=True, stderr=subprocess.DEVNULL)
                            subprocess.run(["docker", "rm", container_id], check=True, stderr=subprocess.DEVNULL)
                            deleted_count += 1
                        except Exception:
                            pass
                
                # Clear the database file
                with open(database_file, 'w') as f:
                    f.write('')
                    
                embed = discord.Embed(
                    title=" All VPS Instances Deleted",
                    description=f"Successfully deleted {deleted_count} VPS instances.",
                    color=0x00ff00
                )
                # Use followup instead of edit_message
                await interaction.followup.send(embed=embed)
                
                # Disable all buttons
                for child in self.children:
                    child.disabled = True
                
            else:
                # Delete single VPS instance
                try:
                    subprocess.run(["docker", "stop", self.container_id], check=True, stderr=subprocess.DEVNULL)
                    subprocess.run(["docker", "rm", self.container_id], check=True, stderr=subprocess.DEVNULL)
                    remove_from_database(self.container_id)
                    
                    embed = discord.Embed(
                        title=" VPS Deleted",
                        description=f"Successfully deleted VPS instance `{self.container_name}`.",
                        color=0x00ff00
                    )
                    # Use followup instead of edit_message
                    await interaction.followup.send(embed=embed)
                    
                    # Disable all buttons
                    for child in self.children:
                        child.disabled = True
                    
                except Exception as e:
                    embed = discord.Embed(
                        title="❌ Error",
                        description=f"Failed to delete VPS instance: {str(e)}",
                        color=0xff0000
                    )
                    await interaction.followup.send(embed=embed)
        except Exception as e:
            # Handle any unexpected errors
            try:
                await interaction.followup.send(f"An error occurred: {str(e)}")
                pass
    
    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # First, acknowledge the interaction to prevent timeout
        await interaction.response.defer(ephemeral=False)
        
        embed = discord.Embed(
            title="🚫 Operation Cancelled",
            description="The delete operation has been cancelled.",
            color=0xffaa00
        )
        # Use followup instead of edit_message
        await interaction.followup.send(embed=embed)
        
        # Disable all buttons
        for child in self.children:
            child.disabled = True


@bot.event
async def on_ready():
    async def update_status():
        while True:
            try:
                with open("database.txt", "r") as f:
                    count = len([line for line in f if line.strip()])
            except FileNotFoundError:
                count = 0

            await bot.change_presence(
                activity=discord.Activity(
                    type=discord.ActivityType.watching,
                    name=f"VPS Deploy | Total: {count} | Gamerzhacker"
                )
            )
            await asyncio.sleep(300)  # update every 5 mins

    bot.loop.create_task(update_status())
    print(f"✅ Bot Ready: {bot.user}")
    
@tasks.loop(seconds=5)
async def change_status():
    try:
        if os.path.exists(database_file):
            with open(database_file, 'r') as f:
                lines = f.readlines()
                instance_count = len(lines)
        else:
            instance_count = 0

        status = f"with {instance_count} Cloud Instances 🌐"
        await bot.change_presence(activity=discord.Game(name=status))
    except Exception as e:
        print(f"Failed to update status: {e}")

@bot.tree.command(name="nodedmin", description="📊 Admin: Lists all VPSs, their details, and SSH commands")
async def nodedmin(interaction: discord.Interaction):
    if not is_admin(interaction.user.id):
        embed = discord.Embed(
            title="❌ Access Denied",
            description="You don't have permission to use this command.",
            color=0xff0000
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    # Use defer to handle potentially longer processing time
    await interaction.response.defer()

    if not os.path.exists(database_file):
        embed = discord.Embed(
            title="VPS Instances",
            description="No VPS data available.",
            color=0xff0000
        )
        await interaction.followup.send(embed=embed)
        return

    embed = discord.Embed(
        title="All VPS Instances",
        description="Detailed information about all VPS instances",
        color=0x00aaff
    )
    
    with open(database_file, 'r') as f:
        lines = f.readlines()
    
    # If there are too many instances, we might need multiple embeds
    embeds = []
    current_embed = embed
    field_count = 0
    
    for line in lines:
        parts = line.strip().split('|')
        
        # Check if we need a new embed (Discord has a 25 field limit per embed)
        if field_count >= 25:
            embeds.append(current_embed)
            current_embed = discord.Embed(
                title="📊 All VPS Instances (Continued)",
                description="Detailed information about all VPS instances",
                color=0x00aaff
            )
            field_count = 0
        
        if len(parts) >= 8:
            user, container_name, ssh_command, ram, cpu, creator, os_type, expiry = parts
            stats = get_container_stats(container_name)
            
            current_embed.add_field(
                name=f"🖥️ {container_name} ({stats['status']})",
                value=f"🪩 **User:** {user}\n"
                      f"💾 **RAM:** {ram}GB\n"
                      f"🔥 **CPU:** {cpu} cores\n"
                      f"🌐 **OS:** {os_type}\n"
                      f"👑 **Creator:** {creator}\n"
                      f"🔑 **SSH:** `{ssh_command}`",
                inline=False
            )
            field_count += 1
        elif len(parts) >= 3:
            user, container_name, ssh_command = parts
            stats = get_container_stats(container_name)
            
            current_embed.add_field(
                name=f"🖥️ {container_name} ({stats['status']})",
                value=f"👤 **User:** {user}\n"
                      f"🔑 **SSH:** `{ssh_command}`",
                inline=False
            )
            field_count += 1
    
    # Add the last embed if it has fields
    if field_count > 0:
        embeds.append(current_embed)
    
    # Send all embeds
    if not embeds:
        await interaction.followup.send("No VPS instances found.")
        return
        
    for i, embed in enumerate(embeds):
        await interaction.followup.send(embed=embed)

@bot.tree.command(name="node", description="☠️ Shows system resource usage and VPS status")
async def node_stats(interaction: discord.Interaction):
    await interaction.response.defer()
    
    system_stats = get_system_stats()
    containers = get_all_containers()
    
    embed = discord.Embed(
        title="🖥️ System Resource Usage",
        description="Current resource usage of the host system",
        color=0x00aaff
    )
    
    embed.add_field(
        name="🔥 Memory Usage",
        value=f"Used: {system_stats['used_memory']} / Total: {system_stats['total_memory']}",
        inline=False
    )
    
    embed.add_field(
        name="💾 Storage Usage",
        value=f"Used: {system_stats['used_disk']} / Total: {system_stats['total_disk']}",
        inline=False
    )
    
    embed.add_field(
        name=f"🧊 VPS Instances ({len(containers)})",
        value="List of all VPS instances and their status:",
        inline=False
    )
    
    for container_info in containers:
        parts = container_info.split('|')
        if len(parts) >= 2:
            container_id = parts[1]
            stats = get_container_stats(container_id)
            embed.add_field(
                name=f"{container_id}",
                value=f"Status: {stats['status']}\nMemory: {stats['memory']}\nCPU: {stats['cpu']}",
                inline=True
            )
    
    await interaction.followup.send(embed=embed)

async def regen_ssh_command(interaction: discord.Interaction, container_name: str):
    user = str(interaction.user)
    container_id = get_container_id_from_database(user, container_name)

    if not container_id:
        embed = discord.Embed(
            title="❌ Not Found",
            description="No active instance found with that name for your user.",
            color=0xff0000
        )
        await interaction.response.send_message(embed=embed)
        return

    try:
        exec_cmd = await asyncio.create_subprocess_exec("docker", "exec", container_id, "tmate", "-F",
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        embed = discord.Embed(
            title="❌ Error",
            description=f"Error executing tmate in Docker container: {e}",
            color=0xff0000
        )
        await interaction.response.send_message(embed=embed)
        return

    ssh_session_line = await capture_ssh_session_line(exec_cmd)
    if ssh_session_line:
        # Update SSH command in database
        if os.path.exists(database_file):
            with open(database_file, 'r') as f:
                lines = f.readlines()
            with open(database_file, 'w') as f:
                for line in lines:
                    if container_id in line:
                        parts = line.strip().split('|')
                        if len(parts) >= 3:
                            parts[2] = ssh_session_line
                            f.write('|'.join(parts) + '\n')
                    else:
                        f.write(line)
        
        # Send DM with new SSH command
        dm_embed = discord.Embed(
            title="🔄 New SSH Session Generated",
            description="Your SSH session has been regenerated successfully.",
            color=0x00ff00
        )
        dm_embed.add_field(
            name="🔑 SSH Connection Command",
            value=f"```{ssh_session_line}```",
            inline=False
        )
        await interaction.user.send(embed=dm_embed)
        
        # Send public success message
        success_embed = discord.Embed(
            title="✅ SSH Session Regenerated",
            description="New SSH session generated. Check your DMs for details.",
            color=0x00ff00
        )
        await interaction.response.send_message(embed=success_embed)
    else:
        error_embed = discord.Embed(
            title="❌ Failed",
            description="Failed to generate new SSH session.",
            color=0xff0000
        )
        await interaction.response.send_message(embed=error_embed)

async def start_server(interaction: discord.Interaction, container_name: str):
    user = str(interaction.user)
    container_id = get_container_id_from_database(user, container_name)

    if not container_id:
        embed = discord.Embed(
            title="❌ Not Found",
            description="No instance found with that name for your user.",
            color=0xff0000
        )
        await interaction.response.send_message(embed=embed)
        return

    await interaction.response.defer()

    try:
        subprocess.run(["docker", "start", container_id], check=True)
        exec_cmd = await asyncio.create_subprocess_exec("docker", "exec", container_id, "tmate", "-F",
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        ssh_session_line = await capture_ssh_session_line(exec_cmd)
        
        if ssh_session_line:
            # Update SSH command in database
            if os.path.exists(database_file):
                with open(database_file, 'r') as f:
                    lines = f.readlines()
                with open(database_file, 'w') as f:
                    for line in lines:
                        if container_id in line:
                            parts = line.strip().split('|')
                            if len(parts) >= 3:
                                parts[2] = ssh_session_line
                                f.write('|'.join(parts) + '\n')
                        else:
                            f.write(line)
            
            # Send DM with SSH command
            dm_embed = discord.Embed(
                title="▶️ VPS Started",
                description=f"Your VPS instance `{container_name}` has been started successfully.",
                color=0x00ff00
            )
            dm_embed.add_field(
                name="🔑 SSH Connection Command",
                value=f"```{ssh_session_line}```",
                inline=False
            )
            
            try:
                await interaction.user.send(embed=dm_embed)
                
                # Public success message
                success_embed = discord.Embed(
                    title="✅ VPS Started",
                    description=f"Your VPS instance `{container_name}` has been started. Check your DMs for connection details.",
                    color=0x00ff00
                )
                await interaction.followup.send(embed=success_embed)
            except discord.Forbidden:
                # If DMs are closed
                warning_embed = discord.Embed(
                    title="⚠️ Cannot Send DM",
                    description="Your VPS has been started, but I couldn't send you a DM with the connection details. Please enable DMs from server members.",
                    color=0xffaa00
                )
                warning_embed.add_field(
                    name="🔑 SSH Connection Command",
                    value=f"```{ssh_session_line}```",
                    inline=False
                )
                await interaction.followup.send(embed=warning_embed)
        else:
            error_embed = discord.Embed(
                title="⚠️ Partial Success",
                description="VPS started, but failed to get SSH session line.",
                color=0xffaa00
            )
            await interaction.followup.send(embed=error_embed)
    except subprocess.CalledProcessError as e:
        error_embed = discord.Embed(
            title="❌ Error",
            description=f"Error starting VPS instance: {e}",
            color=0xff0000
        )
        await interaction.followup.send(embed=error_embed)

async def stop_server(interaction: discord.Interaction, container_name: str):
    user = str(interaction.user)
    container_id = get_container_id_from_database(user, container_name)

    if not container_id:
        embed = discord.Embed(
            title="❌ Not Found",
            description="No instance found with that name for your user.",
            color=0xff0000
        )
        await interaction.response.send_message(embed=embed)
        return

    await interaction.response.defer()

    try:
        subprocess.run(["docker", "stop", container_id], check=True)
        success_embed = discord.Embed(
            title="⏹️ VPS Stopped",
            description=f"Your VPS instance `{container_name}` has been stopped. You can start it again with `/start {container_name}`",
            color=0x00ff00
        )
        await interaction.followup.send(embed=success_embed)
    except subprocess.CalledProcessError as e:
        error_embed = discord.Embed(
            title="❌ Error",
            description=f"Failed to stop VPS instance: {str(e)}",
            color=0xff0000
        )
        await interaction.followup.send(embed=error_embed)

async def restart_server(interaction: discord.Interaction, container_name: str):
    user = str(interaction.user)
    container_id = get_container_id_from_database(user, container_name)

    if not container_id:
        embed = discord.Embed(
            title="❌ Not Found",
            description="No instance found with that name for your user.",
            color=0xff0000
        )
        await interaction.response.send_message(embed=embed)
        return

    await interaction.response.defer()

    try:
        subprocess.run(["docker", "restart", container_id], check=True)
        exec_cmd = await asyncio.create_subprocess_exec("docker", "exec", container_id, "tmate", "-F",
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        ssh_session_line = await capture_ssh_session_line(exec_cmd)
        
        if ssh_session_line:
            # Update SSH command in database
            if os.path.exists(database_file):
                with open(database_file, 'r') as f:
                    lines = f.readlines()
                with open(database_file, 'w') as f:
                    for line in lines:
                        if container_id in line:
                            parts = line.strip().split('|')
                            if len(parts) >= 3:
                                parts[2] = ssh_session_line
                                f.write('|'.join(parts) + '\n')
                        else:
                            f.write(line)
            
            # Send DM with SSH command
            dm_embed = discord.Embed(
                title="🔄 VPS Restarted",
                description=f"Your VPS instance `{container_name}` has been restarted successfully.",
                color=0x00ff00
            )
            dm_embed.add_field(
                name="🔑 SSH Connection Command",
                value=f"```{ssh_session_line}```",
                inline=False
            )
            
            try:
                await interaction.user.send(embed=dm_embed)
                
                # Public success message
                success_embed = discord.Embed(
                    title="✅ VPS Restarted",
                    description=f"Your VPS instance `{container_name}` has been restarted. Check your DMs for connection details.",
                    color=0x00ff00
                )
                await interaction.followup.send(embed=success_embed)
            except discord.Forbidden:
                # If DMs are closed
                warning_embed = discord.Embed(
                    title="⚠️ Cannot Send DM",
                    description="Your VPS has been restarted, but I couldn't send you a DM with the connection details. Please enable DMs from server members.",
                    color=0xffaa00
                )
                warning_embed.add_field(
                    name="🔑 SSH Connection Command",
                    value=f"```{ssh_session_line}```",
                    inline=False
                )
                await interaction.followup.send(embed=warning_embed)
        else:
            error_embed = discord.Embed(
                title="⚠️ Partial Success",
                description="VPS restarted, but failed to get SSH session line.",
                color=0xffaa00
            )
            await interaction.followup.send(embed=error_embed)
    except subprocess.CalledProcessError as e:
        error_embed = discord.Embed(
            title="❌ Error",
            description=f"Error restarting VPS instance: {e}",
            color=0xff0000
        )
        await interaction.followup.send(embed=error_embed)

async def capture_output(process, keyword):
    while True:
        output = await process.stdout.readline()
        if not output:
            break
        output = output.decode('utf-8').strip()
        if keyword in output:
            return output
    return None

@bot.tree.command(name="port-add", description="🔌 Adds a port forwarding rule")
@app_commands.describe(container_name="The name of the container", container_port="The port in the container")
async def port_add(interaction: discord.Interaction, container_name: str, container_port: int):
    embed = discord.Embed(
        title="🔄 Setting Up Port Forwarding",
        description="Setting up port forwarding. This might take a moment...",
        color=0x00aaff
    )
    await interaction.response.send_message(embed=embed)

    public_port = generate_random_port()

    # Set up port forwarding inside the container
    command = f"ssh -o StrictHostKeyChecking=no -R {public_port}:localhost:{container_port} serveo.net -N -f"

    try:
        # Run the command in the background using Docker exec
        await asyncio.create_subprocess_exec(
            "docker", "exec", container_name, "bash", "-c", command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )

        # Respond with the port and public IP
        success_embed = discord.Embed(
            title="✅ Port Forwarding Successful",
            description=f"Your service is now accessible from the internet.",
            color=0x00ff00
        )
        success_embed.add_field(
            name="🌐 Connection Details",
            value=f"**Host:** {PUBLIC_IP}\n**Port:** {public_port}",
            inline=False
        )
        await interaction.followup.send(embed=success_embed)

    except Exception as e:
        error_embed = discord.Embed(
            title="❌ Error",
            description=f"An unexpected error occurred: {e}",
            color=0xff0000
        )
        await interaction.followup.send(embed=error_embed)

@bot.tree.command(name="port-http", description="🌐 Forward HTTP traffic to your container")
@app_commands.describe(container_name="The name of your container", container_port="The port inside the container to forward")
async def port_forward_website(interaction: discord.Interaction, container_name: str, container_port: int):
    embed = discord.Embed(
        title="🔄 Setting Up HTTP Forwarding",
        description="Setting up HTTP forwarding. This might take a moment...",
        color=0x00aaff
    )
    await interaction.response.send_message(embed=embed)
    
    try:
        exec_cmd = await asyncio.create_subprocess_exec(
            "docker", "exec", container_name, "ssh", "-o", "StrictHostKeyChecking=no", "-R", f"80:localhost:{container_port}", "serveo.net",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        url_line = await capture_output(exec_cmd, "Forwarding HTTP traffic from")
        
        if url_line:
            url = url_line.split(" ")[-1]
            success_embed = discord.Embed(                title="✅ HTTP Forwarding Successful",
                description=f"Your web service is now accessible from the internet.",
                color=0x00ff00
            )
            success_embed.add_field(
                name="🌐 Website URL",
                value=f"[{url}](https://{url})",
                inline=False
            )
            await interaction.followup.send(embed=success_embed)
        else:
            error_embed = discord.Embed(
                title="❌ Error",
                description="Failed to set up HTTP forwarding. Please try again later.",
                color=0xff0000
            )
            await interaction.followup.send(embed=error_embed)
    except Exception as e:
        error_embed = discord.Embed(
            title="❌ Error",
            description=f"An unexpected error occurred: {e}",
            color=0xff0000
        )
        await interaction.followup.send(embed=error_embed)

@bot.tree.command(name="deploy", description="🚀 Admin: Deploy a new VPS instance")
@app_commands.describe(
    ram="RAM allocation in GB (max 16gb)",
    cpu="CPU cores (max 24)",
    target_user="Discord user ID to assign the VPS to",
    container_name="Custom container name (default: auto-generated)",
    expiry="Time until expiry (e.g. 1d, 2h, 30m, 45s, 1y, 3M)"
)
async def deploy(
    interaction: discord.Interaction, 
    ram: int = 16000, 
    cpu: int = 40, 
    target_user: str = None,
    container_name: str = None,
    expiry: str = None
):
    # Check if user is admin
    if interaction.user.id not in ADMIN_IDS:
        embed = discord.Embed(
            title="❌ Access Denied",
            description="You don't have permission to use this command.",
            color=0xff0000
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    # Validate parameters
    if ram > 16000:
        ram = 90000
    if cpu > 40:
        cpu = 90
    
    # Set target user
    user_id = target_user if target_user else str(interaction.user.id)
    user = target_user if target_user else str(interaction.user)
    
    # Generate container name if not provided
    if not container_name:
        username = interaction.user.name.replace(" ", "_")
        random_string = generate_random_string(8)
        container_name = f"VPS_{username}_{random_string}"
    
    # Parse expiry time
    expiry_seconds = parse_time_to_seconds(expiry)
    expiry_date = format_expiry_date(expiry_seconds) if expiry_seconds else None
    
    # Show OS selection dropdown
    embed = discord.Embed(
        title="**🖥️ Select Operating System**",
        description="** 🔍 Please select the operating system for your VPS instance **",
        color=0x00aaff
    )
    
    async def os_selected_callback(interaction, selected_os):
        await deploy_with_os(interaction, selected_os, ram, cpu, user_id, user, container_name, expiry_date)
    
    view = OSSelectView(os_selected_callback)
    await interaction.response.send_message(embed=embed, view=view)

async def deploy_with_os(interaction, os_type, ram, cpu, user_id, user, container_name, expiry_date):
    # Prepare response
    embed = discord.Embed(
        title="**🛠️ Creating VPS**",
        description=f"**💾 RAM: {ram}GB\n**"
                    f"**🔥 CPU: {cpu} cores\n**"
                    f" 🧊**OS:** {os_type}\n"
                    f"**🧊 conatiner name: {user}\n**"
                    f"**⌚ Expiry: {expiry_date if expiry_date else 'None'}**",
        color=0x00ff00
    )
    await interaction.followup.send(embed=embed)
    
    # Select image based on OS type
    image = get_docker_image_for_os(os_type)
    
    try:
        # Create container with resource limits
        container_id = subprocess.check_output([
            "docker", "run", "-itd", 
            "--privileged", 
            "--cap-add=ALL",
            f"--memory={ram}g",
            f"--cpus={cpu}",
            "--name", container_name,
            image
        ]).strip().decode('utf-8')
    except subprocess.CalledProcessError as e:
        error_embed = discord.Embed(
            title="❌ Error",
            description=f"Error creating Docker container: {e}",
            color=0xff0000
        )
        await interaction.followup.send(embed=error_embed)
        return

    try:
        exec_cmd = await asyncio.create_subprocess_exec("docker", "exec", container_name, "tmate", "-F",
                                                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except Exception as e:
        error_embed = discord.Embed(
            title="❌ Error",
            description=f"Error executing tmate in Docker container: {e}",
            color=0xff0000
        )
        await interaction.followup.send(embed=error_embed)
        
        # Clean up container
        subprocess.run(["docker", "stop", container_name], check=False)
        subprocess.run(["docker", "rm", container_name], check=False)
        return

    ssh_session_line = await capture_ssh_session_line(exec_cmd)
    if ssh_session_line:
        # Add to database with extended information
        add_to_database(
            user, 
            container_name, 
            ssh_session_line, 
            ram_limit=ram, 
            cpu_limit=cpu, 
            creator=str(interaction.user),
            expiry=expiry_date,
            os_type=os_type_to_display_name(os_type)
        )
        
        # Create a DM embed with detailed information
        dm_embed = discord.Embed(
            description=f"**✅ VPS created successfully. Check your DM for details.**",
            color=0x00ff00
        )
        
        
        dm_embed.add_field(name="🔑 SSH Connection Command", value=f"```{ssh_session_line}```", inline=False)
        dm_embed.add_field(name="💾 RAM Allocation", value=f"{ram}GB", inline=True)
        dm_embed.add_field(name="🔥 CPU Cores", value=f"{cpu} cores", inline=True)
        dm_embed.add_field(name="🧊 Container Name", value=container_name, inline=False)
        dm_embed.add_field(name="💾 Storage", value=f"10000 GB (Shared storage)", inline=True)
        dm_embed.add_field(name="🔒 Password", value="hk-i9", inline=False)
        
        dm_embed.set_footer(text="Keep this information safe and private!")
        
        # Try to send DM to target user
        target_user_obj = await bot.fetch_user(int(user_id))
        
        try:
            await target_user_obj.send(embed=dm_embed)
            
            # Public success message
            success_embed = discord.Embed(
                title="**✅ Create VPS hk-i10 Successfully**",
                description=f"** 🎉 VPS instance has been created for <@{user_id}>. They should check their DMs for connection details.**",
                color=0x00ff00
            )
            await interaction.followup.send(embed=success_embed)
            
        except discord.Forbidden:
            # If DMs are closed
            warning_embed = discord.Embed(
                title="**🔍 Cannot Send DM**",
                description=f"**VPS has been created, but I couldn't send a DM with the connection details to <@{user_id}>. Please enable DMs from server members.**",
                color=0xffaa00
            )
            warning_embed.add_field(name="🔑 SSH Connection Command", value=f"```{ssh_session_line}```", inline=False)
            await interaction.followup.send(embed=warning_embed)
    else:
        # Clean up container if SSH session couldn't be established
        try:
            subprocess.run(["docker", "stop", container_name], check=False)
            subprocess.run(["docker", "rm", container_name], check=False)
        except Exception:
            pass
        
        error_embed = discord.Embed(
            title="❌ Deployment Failed",
            description="Failed to establish SSH session. The container has been cleaned up. Please try again.",
            color=0xff0000
        )
        await interaction.followup.send(embed=error_embed)

def os_type_to_display_name(os_type):
    """Convert OS type to display name"""
    os_map = {
        "ubuntu": "Ubuntu 22.04",
        "debian": "Debian 12"
    }
    return os_map.get(os_type, "Unknown OS")

def get_docker_image_for_os(os_type):
    """Get Docker image name for OS type"""
    os_map = {
        "ubuntu": "ubuntu-22.04-with-tmate",
        "debian": "debian-with-tmate"
    }
    return os_map.get(os_type, "ubuntu-22.04-with-tmate")

# Tips navigation view
class TipsView(View):
    def __init__(self):
        super().__init__(timeout=300)  # 5 minute timeout
        self.current_page = 0
        self.tips = [
            {
                "title": "🔑 SSH Connection Tips",
                "description": "• Use `ssh-keygen` to create SSH keys for passwordless login\n"
                              "• Forward ports with `-L` flag: `ssh -L 8080:localhost:80 user@host`\n"
                              "• Keep connections alive with `ServerAliveInterval=60` in SSH config\n"
                              "• Use `tmux` or `screen` to keep sessions running after disconnect"
            },
            {
                "title": "🛠️ System Management",
                "description": "• Update packages regularly: `apt update && apt upgrade`\n"
                              "• Monitor resources with `htop` or `top`\n"
                              "• Check disk space with `df -h`\n"
                              "• View logs with `journalctl` or check `/var/log/`"
            },
            {
                "title": "🌐 Web Hosting Tips",
                "description": "• Install Nginx or Apache for web hosting\n"
                              "• Secure with Let's Encrypt for free SSL certificates\n"
                              "• Use PM2 to manage Node.js applications\n"
                              "• Set up proper firewall rules with `ufw`"
            },
            {
                "title": "📊 Performance Optimization",
                "description": "• Limit resource-intensive processes\n"
                              "• Use caching for web applications\n"
                              "• Configure swap space for low-memory situations\n"
                              "• Optimize database queries and indexes"
            },
            {
                "title": "🔒 Security Best Practices",
                "description": "• Change default passwords immediately\n"
                              "• Disable root SSH login\n"
                              "• Keep software updated\n"
                              "• Use `fail2ban` to prevent brute force attacks\n"
                              "• Regularly backup important data"
            }
        ]
    
    @discord.ui.button(label="◀️ Previous", style=discord.ButtonStyle.secondary)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = (self.current_page - 1) % len(self.tips)
        await interaction.response.edit_message(embed=self.get_current_embed(), view=self)
    
    @discord.ui.button(label="▶️ Next", style=discord.ButtonStyle.primary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page = (self.current_page + 1) % len(self.tips)
        await interaction.response.edit_message(embed=self.get_current_embed(), view=self)
    
    def get_current_embed(self):
        tip = self.tips[self.current_page]
        embed = discord.Embed(
            title=tip["title"],
            description=tip["description"],
            color=0x00aaff
        )
        embed.set_footer(text=f"Tip {self.current_page + 1}/{len(self.tips)}")
        return embed

@bot.tree.command(name="tips", description="💡 Shows useful tips for managing your VPS")
async def tips_command(interaction: discord.Interaction):
    view = TipsView()
    embed = view.get_current_embed()
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="delete", description="Delete your VPS instance")
@app_commands.describe(container_name="The name of your container")
async def delete_server(interaction: discord.Interaction, container_name: str):
    user = str(interaction.user)
    container_id = get_container_id_from_database(user, container_name)

    if not container_id:
        embed = discord.Embed(
            title="❌ Not Found",
            description="No instance found with that name for your user.",
            color=0xff0000
        )
        await interaction.response.send_message(embed=embed)
        return

    # Create confirmation dialog
    confirm_embed = discord.Embed(
        title="**⚠️ Confirm Deletion**",
        description=f"**Are you sure you want to delete VPS instance `{container_name}`? This action cannot be undone.**",
        color=0xffaa00
    )
    
    view = ConfirmView(container_id, container_name)
    await interaction.response.send_message(embed=confirm_embed, view=view)

@bot.tree.command(name="delete-all", description="🗑️ Admin: Delete all VPS instances")
async def delete_all_servers(interaction: discord.Interaction):
    # Check if user is admin
    if interaction.user.id not in ADMIN_IDS:
        embed = discord.Embed(
            title="**❌ Access Denied**",
            description="**You don't have permission to use this command.**",
            color=0xff0000
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    # Get count of all containers
    containers = get_all_containers()
    
    # Create confirmation dialog
    confirm_embed = discord.Embed(
        title="**⚠️ Confirm Mass Deletion**",
        description=f"**Are you sure you want to delete ALL {len(containers)} VPS instances? This action cannot be undone.**",
        color=0xffaa00
    )
    
    view = ConfirmView(None, None, is_delete_all=True)
    await interaction.response.send_message(embed=confirm_embed, view=view)

@bot.tree.command(name="list", description="📋 List all your VPS instances")
async def list_servers(interaction: discord.Interaction):
    user = str(interaction.user)
    servers = get_user_servers(user)

    await interaction.response.defer()

    if not servers:
        embed = discord.Embed(
            title="**📋 Your VPS Instances",
            description="**You don't have any VPS instances. Use `/deploy` to create one!**",
            color=0x00aaff
        )
        await interaction.followup.send(embed=embed)
        return

    embed = discord.Embed(
        title="**📋 Your VPS Instances**",
        description=f"**You have {len(servers)} VPS instance(s)**",
        color=0x00aaff
    )

    for server in servers:
        parts = server.split('|')
        container_id = parts[1]
        
        # Get container status
        try:
            container_info = subprocess.check_output(["docker", "inspect", "--format", "{{.State.Status}}", container_id]).decode().strip()
            status = "🟢 Running" if container_info == "running" else "🔴 Stopped"
            status = "🔴 Stopped"
        
        # Get resource limits and other details
        if len(parts) >= 8:
            ram_limit, cpu_limit, creator, os_type, expiry = parts[3], parts[4], parts[5], parts[6], parts[7]
            
            embed.add_field(
                name=f"🖥️ {container_id} ({status})",
                value=f"💾 **RAM:** {ram_limit}GB\n"
                      f"🔥 **CPU:** {cpu_limit} cores\n"
                      f"💾 **Storage:** 10000 GB (Shared)\n"
                      f" 🧊**OS:** {os_type}\n"
                      f"👑 **Created by:** {creator}\n"
                      f"⏱️ **Expires:** {expiry}",
                inline=False
            )
        else:
            embed.add_field(
                name=f"🖥️ {container_id} ({status})",
                value=f"💾 **RAM:** 16GB\n"
                      f"🔥 **CPU:** 40 core\n"
                      f"💾 **Storage:** 10000 GB (Shared)\n"
                      f"🧊 **OS:** Ubuntu 22.04",
                inline=False
            )

    await interaction.followup.send(embed=embed)

@bot.tree.command(name="regen-ssh", description="🔄 Regenerate SSH session for your instance")
@app_commands.describe(container_name="The name of your container")
async def regen_ssh(interaction: discord.Interaction, container_name: str):
    await regen_ssh_command(interaction, container_name)

@bot.tree.command(name="start", description="▶️ Start your VPS instance")
@app_commands.describe(container_name="The name of your container")
async def start(interaction: discord.Interaction, container_name: str):
    await start_server(interaction, container_name)

@bot.tree.command(name="stop", description="⏹️ Stop your VPS instance")
@app_commands.describe(container_name="The name of your container")
async def stop(interaction: discord.Interaction, container_name: str):
    await stop_server(interaction, container_name)

@bot.tree.command(name="restart", description="🔄 Restart your VPS instance")
@app_commands.describe(container_name="The name of your container")
async def restart(interaction: discord.Interaction, container_name: str):
    await restart_server(interaction, container_name)

@bot.tree.command(name="ping", description="🏓 Check the bot's latency")
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    embed = discord.Embed(
        title="🏓 Pong!",
        description=f"Latency: {latency}ms",
        color=0x00ff00
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="help", description="❓ Shows the help message")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="**🌟 VPS Bot Help**",
        description="** Here are all the available commands:**",
        color=0x00aaff
    )
    
    # User commands
    embed.add_field(
        name="📋 User Commands",
        value="Commands available to all users:",
        inline=False
    )
    embed.add_field(name="/start <container_name>", value="Start your VPS instance", inline=True)
    embed.add_field(name="/stop <container_name>", value="Stop your VPS instance", inline=True)
    embed.add_field(name="/restart <container_name>", value="Restart your VPS instance", inline=True)
    embed.add_field(name="/regen-ssh <container_name>", value="Regenerate SSH credentials", inline=True)
    embed.add_field(name="/list", value="List all your VPS instances", inline=True)
    embed.add_field(name="/delete <container_name>", value="Delete your VPS instance", inline=True)
    embed.add_field(name="/port-add <container_name> <port>", value="Forward a port", inline=True)
    embed.add_field(name="/port-http <container_name> <port>", value="Forward HTTP traffic", inline=True)
    embed.add_field(name="/ping", value="Check bot latency", inline=True)
    embed.add_field(name="/botinfo", value="Check bot Developer", inline=True)
    
    # Admin commands
    if interaction.user.id in ADMIN_IDS:
        embed.add_field(
            name="👑 Admin Commands",
            value="Commands available only to admins:",
            inline=False
        )
        embed.add_field(name="/deploy", value="Deploy a new VPS with custom settings", inline=True)
        embed.add_field(name="/node", value="View system resource usage", inline=True)
        embed.add_field(name="/nodedmin", value="List all VPS instances with details", inline=True)
        embed.add_field(name="/delete-all", value="Delete all VPS instances", inline=True)
    
    await interaction.response.send_message(embed=embed)

ACCESS_FILE = "access.txt"
SHARE_LIMIT = 3

# === Access Sharing ===
def get_shared_users(container_name):
    if not os.path.exists(ACCESS_FILE):
        return []
    with open(ACCESS_FILE, 'r') as f:
        return [line.split('|')[1].strip() for line in f if line.startswith(container_name + "|")]

def add_shared_user(container_name, user_id):
    users = get_shared_users(container_name)
    if str(user_id) not in users and len(users) < SHARE_LIMIT:
        with open(ACCESS_FILE, 'a') as f:
            f.write(f"{container_name}|{user_id}\n")

def remove_shared_user(container_name, user_id):
    if not os.path.exists(ACCESS_FILE):
        return
    with open(ACCESS_FILE, 'r') as f:
        lines = f.readlines()
    with open(ACCESS_FILE, 'w') as f:
        for line in lines:
            if line.strip() != f"{container_name}|{user_id}":
                f.write(line)

def remove_all_shares(container_name):
    if not os.path.exists(ACCESS_FILE):
        return
    with open(ACCESS_FILE, 'r') as f:
        lines = f.readlines()
    with open(ACCESS_FILE, 'w') as f:
        for line in lines:
            if not line.startswith(container_name + "|"):
                f.write(line)

def has_access(user_id, container_name):
    servers = get_user_servers(str(user_id))  # define yourself
    for line in servers:
        if container_name in line:
            return True
    return str(user_id) in get_shared_users(container_name)

# === Invite / Boost Verification ===
async def has_required_invites(user: discord.User, required: int):
    invites = 0
    for guild in user.mutual_guilds:
        try:
            all_invites = await guild.invites()
            for invite in all_invites:
                if invite.inviter and invite.inviter.id == user.id:
                    invites += invite.uses
            continue
    return invites >= required

def has_required_boost(member: discord.Member, required: int):
    return member.premium_since is not None and member.guild.premium_subscription_count >= required

# === Fix: string join error ===
def display_shared_users(users):
    return "\n".join(f"<@{uid}>" for uid in users)

# === /create Command ===
class RewardSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="🎉 Invite: 8 Invites = 16GB", value="invite_8"),
            discord.SelectOption(label="🎉 Invite: 15 Invites = 32GB", value="invite_15"),
            discord.SelectOption(label="🚀 Boost: 1 Boost = 16GB", value="boost_1"),
            discord.SelectOption(label="🚀 Boost: 2 Boost = 32GB", value="boost_2"),
        ]
        super().__init__(placeholder="Select your reward plan", options=options)

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        ram = 16000
        cpu = 40
        user = interaction.user
        member = interaction.guild.get_member(user.id)

        if value == "invite_8" and not await has_required_invites(user, 8):
            await interaction.response.send_message("❌ You need at least 8 invites to claim this reward.", ephemeral=True)
            return
        elif value == "invite_15" and not await has_required_invites(user, 15):
            ram = 32000
            await interaction.response.send_message("❌ You need at least 15 invites to claim this reward.", ephemeral=True)
            return
        elif value == "boost_1" and not has_required_boost(member, 1):
            await interaction.response.send_message("❌ You must boost the server to claim this reward.", ephemeral=True)
            return
        elif value == "boost_2" and not has_required_boost(member, 2):
            ram = 32000
            await interaction.response.send_message("❌ You must boost the server with 2 boosts.", ephemeral=True)
            return

        username = user.name.replace(" ", "_")
        container_name = f"VPS_{username}_{generate_random_string(6)}"
        expiry = format_expiry_date(parse_time_to_seconds("7d"))

        async def os_selected(interaction2, os_type):
            await deploy_with_os(interaction2, os_type, ram, cpu, str(user.id), str(user.id), container_name, expiry)

        embed = discord.Embed(
            title="📀 Select Operating System",
            description="✅ Verified! Now choose your preferred OS.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, view=OSSelectView(os_selected), ephemeral=True)

class RewardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(RewardSelect())

@bot.tree.command(name="create", description="🎁 Claim a VPS reward by invite or boost")
async def create(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎁 VPS Reward Claim",
        description="Select your reward type. Invite-based or Boost-based.",
        color=discord.Color.blurple()
    )
    await interaction.response.send_message(embed=embed, view=RewardView(), ephemeral=True)

@bot.tree.command(name="manage", description="🧰 Manage your VPS or shared ones")
async def manage(interaction: discord.Interaction):
    userid = str(interaction.user.id)

    servers = get_user_servers(userid)
    shared = []

    try:
        with open("access.txt", "r") as f:
            for line in f:
                vps, uid = line.strip().split("|")
                if uid == userid:
                    shared.append(vps)
    except FileNotFoundError:
        pass

    if not servers and not shared:
        return await interaction.response.send_message("❌ You have no VPS or shared access.", ephemeral=True)

    all_vps = servers + shared
    container_name = all_vps[0].split('|')[0] if '|' in all_vps[0] else all_vps[0]

    # Check if container exists
    check_container = os.popen(f"docker ps -a --format '{{{{.Names}}}}'").read()
    if container_name not in check_container:
        return await interaction.response.send_message("❌ VPS container not found.", ephemeral=True)

    # VPS status
    running = os.popen(f"docker inspect -f '{{{{.State.Running}}}}' {container_name}").read().strip()
    status = "🟢 Online" if running == "true" else "🔴 Offline"
    color = 0x2ecc71 if running == "true" else 0xe74c3c

    embed = discord.Embed(
        title=f"🖥️ Managing VPS: `{container_name}`",
        description=f"**Status:** {status}",
        color=color
    )
    embed.set_image(url="https://www.imghippo.com/i/bRzC6045UZ.png")
    embed.set_thumbnail(url="https://www.imghippo.com/i/PXAV9041Yyw.png")

    class CmdModal(discord.ui.Modal, title="📥 Run Command on VPS"):
        command = discord.ui.TextInput(label="Enter your command", style=discord.TextStyle.paragraph)

        async def on_submit(self, interaction2):
            try:
                output = os.popen(f'docker exec {container_name} bash -c "{self.command.value}"').read()
                output = output[:1900] + '...' if len(output) > 1900 else output
                await interaction2.response.send_message(f"📤 Output:\n```{output}```", ephemeral=True)
            except Exception as e:
                await interaction2.response.send_message(f"❌ Error: {e}", ephemeral=True)

    class OSSelect(discord.ui.Select):
        def __init__(self):
            super().__init__(placeholder="📀 Select OS to reinstall", options=[
                discord.SelectOption(label="Ubuntu 22.04", value="ubuntu-22.04"),
                discord.SelectOption(label="Ubuntu 20.04", value="ubuntu-20.04"),
                discord.SelectOption(label="Debian 12", value="debian-12"),
                discord.SelectOption(label="Debian 11", value="debian-11")
            ])

        async def callback(self, interaction2):
            os_choice = self.values[0]
            await interaction2.response.send_message(f"📀 Reinstalling VPS with `{os_choice}` (demo)", ephemeral=True)

    class ReinstallView(discord.ui.View):
        def __init__(self):
            super().__init__()
            self.add_item(OSSelect())

    class ManageButtons(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)

        @discord.ui.button(label="✅ Start", style=discord.ButtonStyle.success)
        async def start(self, i, b): 
            try:
                os.system(f"docker start {container_name}")
                await i.response.send_message("✅ VPS started.", ephemeral=True)
            except Exception as e:
                await i.response.send_message(f"❌ Error: {e}", ephemeral=True)

        @discord.ui.button(label="🛑 Stop", style=discord.ButtonStyle.danger)
        async def stop(self, i, b): 
            try:
                os.system(f"docker stop {container_name}")
                await i.response.send_message("🛑 VPS stopped.", ephemeral=True)
            except Exception as e:
                await i.response.send_message(f"❌ Error: {e}", ephemeral=True)

        @discord.ui.button(label="🔁 Restart", style=discord.ButtonStyle.primary)
        async def restart(self, i, b): 
            try:
                os.system(f"docker restart {container_name}")
                await i.response.send_message("🔁 VPS restarted.", ephemeral=True)
            except Exception as e:
                await i.response.send_message(f"❌ Error: {e}", ephemeral=True)

        @discord.ui.button(label="📊 Status", style=discord.ButtonStyle.secondary)
        async def status(self, i, b): 
            running = os.popen(f"docker inspect -f '{{{{.State.Running}}}}' {container_name}").read().strip()
            stat = "🟢 Online" if running == "true" else "🔴 Offline"
            await i.response.send_message(f"📶 VPS is: **{stat}**", ephemeral=True)

        @discord.ui.button(label="🖥️ Run CMD", style=discord.ButtonStyle.secondary)
        async def cmd(self, i, b): 
            await i.response.send_modal(CmdModal())

        @discord.ui.button(label="🔁 Reinstall OS", style=discord.ButtonStyle.secondary)
        async def reinstall(self, i, b): 
            await i.response.send_message("📀 Select new OS to reinstall:", view=ReinstallView(), ephemeral=True)

        @discord.ui.button(label="🗑️ Delete VPS", style=discord.ButtonStyle.danger)
        async def delete(self, i, b): 
            try:
                os.system(f"docker stop {container_name}")
                os.system(f"docker rm {container_name}")
                await i.response.send_message(f"🗑️ `{container_name}` deleted.", ephemeral=True)
            except Exception as e:
                await i.response.send_message(f"❌ Error: {e}", ephemeral=True)

    await interaction.response.send_message(embed=embed, view=ManageButtons(), ephemeral=True)

        @discord.ui.button(label="🔁 Reinstall OS", style=discord.ButtonStyle.secondary)
        async def reinstall(self, i, b): 
            await i.response.send_message("📀 Select new OS to reinstall:", view=ReinstallView(), ephemeral=True)

        @discord.ui.button(label="🗑️ Delete VPS", style=discord.ButtonStyle.danger)
        async def delete(self, i, b): 
            os.system(f"docker stop {container_name}")
            os.system(f"docker rm {container_name}")
            await i.response.send_message(f"🗑️ `{container_name}` deleted.", ephemeral=True)

    await interaction.response.send_message(embed=embed, view=ManageButtons(), ephemeral=True)

# === /myshares ===
@bot.tree.command(name="myshares", description="📋 List all users you've shared VPS access with")
async def myshares(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    if not os.path.exists(ACCESS_FILE):
        await interaction.response.send_message("You haven't shared any VPS access yet.", ephemeral=True)
        return

    owned_vps = [line.split('|')[0] for line in get_user_servers(user_id)]
    shared_data = {}
    with open(ACCESS_FILE, 'r') as f:
        for line in f:
            container, shared_user = line.strip().split('|')
            if container in owned_vps:
                shared_data.setdefault(container, []).append(shared_user)

    if not shared_data:
        await interaction.response.send_message("You haven’t shared access to any VPS.", ephemeral=True)
        return

    embed = discord.Embed(title="👥 Shared VPS Access", color=0x00aaff)
    for vps, users in shared_data.items():
        display = "\n".join(f"<@{uid}>" for uid in users)
        embed.add_field(name=vps, value=display or "No users", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


# === /revokeshareall ===
@bot.tree.command(name="revokeshareall", description="🧹 Remove all shared access from a VPS")
@app_commands.describe(container_name="Your VPS name")
async def revokeshareall(interaction: discord.Interaction, container_name: str):
    user_id = str(interaction.user.id)
    if not has_access(user_id, container_name):
        await interaction.response.send_message("❌ You don’t have access to this VPS.", ephemeral=True)
        return

    remove_all_shares(container_name)
    await interaction.response.send_message(f"✅ All users removed from `{container_name}`.", ephemeral=True)


# === /sharesof (Admin only) ===
@bot.tree.command(name="sharesof", description="🔍 Admin: Check shared users of a user's VPS")
@app_commands.describe(userid="Target user ID")
async def sharesof(interaction: discord.Interaction, userid: str):
    if interaction.user.id not in ADMIN_IDS:
        await interaction.response.send_message("❌ Only admins can use this command.", ephemeral=True)
        return

    owned_vps = [line.split('|')[0] for line in get_user_servers(userid)]
    if not owned_vps:
        await interaction.response.send_message("This user doesn't own any VPS.", ephemeral=True)
        return

    embed = discord.Embed(title=f"📦 VPS Shares of <@{userid}>", color=0x00aaff)
    for vps in owned_vps:
        shared = get_shared_users(vps)
        embed.add_field(name=vps, value="\n".join(f"<@{u}>" for u in shared) or "No shares", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="suspendvps", description="❌ Admin: Suspend all VPS of a user")
@app_commands.describe(usertag="The user whose VPS you want to suspend")
async def suspendvps(interaction: discord.Interaction, usertag: discord.User):
    if interaction.user.id not in ADMIN_IDS:
        await interaction.response.send_message("❌ Only admins can use this command.", ephemeral=True)
        return

    user_id = str(usertag.id)
    containers = [line.split('|')[0] for line in get_user_servers(user_id)]

    if not containers:
        await interaction.response.send_message("⚠️ No VPS found for that user.", ephemeral=True)
        return

    for container in containers:
        os.system(f"docker pause {container}")

    await interaction.response.send_message(f"⛔ Suspended VPS: `{', '.join(containers)}`", ephemeral=True)


@bot.tree.command(name="unsuspendvps", description="✅ Admin: Unsuspend all VPS of a user")
@app_commands.describe(usertag="The user whose VPS you want to unsuspend")
async def unsuspendvps(interaction: discord.Interaction, usertag: discord.User):
    if interaction.user.id not in ADMIN_IDS:
        await interaction.response.send_message("❌ Only admins can use this command.", ephemeral=True)
        return

    user_id = str(usertag.id)
    containers = [line.split('|')[0] for line in get_user_servers(user_id)]

    if not containers:
        await interaction.response.send_message("⚠️ No VPS found for that user.", ephemeral=True)
        return

    for container in containers:
        os.system(f"docker unpause {container}")

    await interaction.response.send_message(f"✅ Unsuspended VPS: `{', '.join(containers)}`", ephemeral=True)

@bot.tree.command(name="sendvps", description="👑 Admin: Send VPS details to a user via DM")
@app_commands.describe(
    ram="RAM in GB",
    cpu="CPU cores",
    ip="IP address",
    port="Port number",
    password="VPS password",
    fullcombo="Full combo format: user@ip:port:pass",
    user="Select the user to send VPS details"
)
async def sendvps(
    interaction: discord.Interaction,
    ram: str,
    cpu: str,
    ip: str,
    port: str,
    password: str,
    fullcombo: str,
    user: discord.User
):
    # Check admin permissions
    if interaction.user.id not in ADMIN_IDS:
        embed = discord.Embed(
            title="❌ Access Denied",
            description="Only Mrsdbd admins can use this command.",
            color=0xff0000
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    # Create the VPS detail embed
    embed = discord.Embed(
        title="✅ VPS Created Successfully!",
        description="Here are your VPS details. Please **save them securely.**",
        color=0x2400ff
    )
    embed.add_field(name="🌐 IP", value=ip, inline=True)
    embed.add_field(name="🔌 Port", value=port, inline=True)
    embed.add_field(name="🔒 Password", value=password, inline=True)
    embed.add_field(name="🧬 Full Combo", value=f"```{fullcombo}```", inline=False)
    embed.add_field(name="💾 RAM", value=f"{ram} GB", inline=True)
    embed.add_field(name="🔥 CPU", value=f"{cpu} cores", inline=True)
    embed.set_footer(text="🔐 Safe your details | Powered by LP NODES")

    try:
        await user.send(embed=embed)
        success = discord.Embed(
            title="📨 DM Sent",
            description=f"Successfully sent VPS details to {user.mention}.",
            color=0x00ff00
        )
        await interaction.response.send_message(embed=success)
    except discord.Forbidden:
        error = discord.Embed(
            title="❌ DM Failed",
            description=f"Could not send DM to {user.mention}. They may have DMs disabled.",
            color=0xff0000
        )
        await interaction.response.send_message(embed=error)

@bot.tree.command(name="sharedipv4", description="🌐 Admin: Setup port forward in VPS and DM SSH info")
@app_commands.describe(container_name="VPS container name", usertag="User to send SSH info")
async def sharedipv4(interaction: discord.Interaction, container_name: str, usertag: discord.User):
    if interaction.user.id not in ADMIN_IDS:
        await interaction.response.send_message("❌ Only admins can use this command.", ephemeral=True)
        return

    await interaction.response.send_message(f"⚙️ Running vps setup inside `{container_name}`...", ephemeral=True)

    # Step 1: Run the port forwarding setup and capture the output
    try:
        result = os.popen(
            f'docker exec {container_name} bash -c "apt update -y && apt install curl -y && bash <(curl -fsSL https://raw.githubusercontent.com/steeldevlol/port/refs/heads/main/install)"'
        ).read()
    except Exception as e:
        await interaction.followup.send(content=f"❌ Error while setting up vps forwarding:\n{e}", ephemeral=True)
        return

    # Step 2: Extract the line with forwarding info
    import re
    match = re.search(r"(tunnel\.steeldev\.space:\d+)", result)
    if not match:
        await interaction.followup.send("❌ Could not find Vps IP and port in response.", ephemeral=True)
        return

    ip_port = match.group(1)
    ssh_cmd = f"ssh root@{ip_port.replace(':', ' -p ')}"

    # Step 3: Send to user
    embed = discord.Embed(
        title="🌐 Createed Vps Successfully",
        description=f"Your VPS is Ssh Port Info `{ip_port}`",
        color=0x00ffcc
    )
    embed.set_thumbnail(url="https://www.imghippo.com/i/PXAV9041Yyw.png")
    embed.set_image(url="https://www.imghippo.com/i/bRzC6045UZ.png")
    embed.add_field(name="SSH Command", value=f"```{ssh_cmd}```", inline=False)
    embed.set_footer(text="DragonCloud • Shared IPv4 Vps Access")

    try:
        await usertag.send(embed=embed)
        await interaction.followup.send(f"✅ Create Vps `{ip_port}` DM sent to {usertag.mention}", ephemeral=True)
        await interaction.followup.send("❌ Could not DM the user.", ephemeral=True)

@bot.tree.command(name="reinstall", description="🔁 Reinstall a user's VPS with selected OS")
@app_commands.describe(usertag="User to reinstall VPS for", os="OS template (ubuntu-22.04 / debian-12)")
async def reinstall(interaction: discord.Interaction, usertag: discord.Member, os: str):
    if str(interaction.user.id) not in ADMIN:  # use your admin list or role check
        return await interaction.response.send_message("❌ You are not authorized.", ephemeral=True)

    userid = str(usertag.id)
    vps_list = get_user_servers(userid)
    if not vps_list:
        return await interaction.response.send_message("❌ No VPS found for this user.", ephemeral=True)

    container_name = vps_list[0].split('|')[0] if '|' in vps_list[0] else vps_list[0]

    # Check if Dockerfile exists
    dockerfile_path = f"os_templates/{os}.Dockerfile"
    if not os.path.exists(dockerfile_path):
        return await interaction.response.send_message("❌ OS template not found.", ephemeral=True)

    await interaction.response.send_message(f"🛠️ Reinstalling `{container_name}` with `{os}`...", ephemeral=True)

    try:
        os.system(f"docker stop {container_name}")
        os.system(f"docker rm {container_name}")
        os.system(f"docker build -t {container_name}-img -f {dockerfile_path} .")
        os.system(f"docker run -d --name {container_name} {container_name}-img")
    except Exception as e:
        return await interaction.followup.send(f"❌ Reinstall failed: {e}", ephemeral=True)

    try:
        await usertag.send(f"✅ Your VPS `{container_name}` has been reinstalled with `{os}`.")
        pass

    await interaction.followup.send(f"✅ Reinstalled VPS `{container_name}` for {usertag.mention} with `{os}`.", ephemeral=True)

bot.run(TOKEN)
