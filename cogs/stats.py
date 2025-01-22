import discord
from discord.ext import commands
from discord.ui import Button, View, Select
import psutil
import platform
import datetime
import asyncio
import collections
from typing import List, Dict, Optional
from dataclasses import dataclass
import matplotlib.pyplot as plt
import io
import humanize
from concurrent.futures import ThreadPoolExecutor

@dataclass
class SystemMetrics:
    cpu_percent: float
    memory_percent: float
    disk_percent: float
    network_sent: int
    network_recv: int
    process_threads: int
    
class MetricsCache:
    def __init__(self, max_size: int = 60):
        self.max_size = max_size
        self.metrics: collections.deque = collections.deque(maxlen=max_size)
        self.last_update = None
        
    def add_metrics(self, metrics: SystemMetrics):
        self.metrics.append(metrics)
        self.last_update = datetime.datetime.utcnow()
        
    def get_average(self, attribute: str, minutes: int = 5) -> float:
        if not self.metrics:
            return 0.0
        recent = list(self.metrics)[-minutes:]
        return sum(getattr(m, attribute) for m in recent) / len(recent)

class ChartGenerator:
    @staticmethod
    def create_usage_chart(cache: MetricsCache) -> bytes:
        plt.figure(figsize=(10, 6))
        plt.style.use('dark_background')
        
        metrics = list(cache.metrics)
        times = range(len(metrics))
        
        plt.plot(times, [m.cpu_percent for m in metrics], label='CPU')
        plt.plot(times, [m.memory_percent for m in metrics], label='Memory')
        plt.plot(times, [m.disk_percent for m in metrics], label='Disk')
        
        plt.title('System Resource Usage Over Time')
        plt.legend()
        plt.grid(True, alpha=0.3)
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        plt.close()
        
        return buf.getvalue()

class StatsView(View):
    def __init__(self, cog: 'AdvancedBotStats'):
        super().__init__(timeout=300)
        self.cog = cog
        self.current_page = 0
        self.chart_mode = False
        
    @discord.ui.select(
        placeholder="Select Statistics Category",
        options=[
            discord.SelectOption(label="System", value="system", description="View system resource usage"),
            discord.SelectOption(label="Bot", value="bot", description="View bot statistics"),
            discord.SelectOption(label="Network", value="network", description="View network statistics"),
            discord.SelectOption(label="Charts", value="charts", description="View usage charts")
        ]
    )
    async def select_category(self, interaction: discord.Interaction, select: Select):
        self.chart_mode = select.values[0] == "charts"
        embed = await self.cog.generate_embed(select.values[0])
        
        if self.chart_mode:
            chart = ChartGenerator.create_usage_chart(self.cog.metrics_cache)
            file = discord.File(io.BytesIO(chart), filename="usage_chart.png")
            embed.set_image(url="attachment://usage_chart.png")
            await interaction.response.edit_message(embed=embed, attachments=[file], view=self)
        else:
            await interaction.response.edit_message(embed=embed, attachments=[], view=self)

    @discord.ui.button(label="🔄 Refresh", style=discord.ButtonStyle.green)
    async def refresh(self, interaction: discord.Interaction, button: Button):
        await self.cog.update_metrics()
        embed = await self.cog.generate_embed("system" if not self.chart_mode else "charts")
        
        if self.chart_mode:
            chart = ChartGenerator.create_usage_chart(self.cog.metrics_cache)
            file = discord.File(io.BytesIO(chart), filename="usage_chart.png")
            embed.set_image(url="attachment://usage_chart.png")
            await interaction.response.edit_message(embed=embed, attachments=[file], view=self)
        else:
            await interaction.response.edit_message(embed=embed, attachments=[], view=self)

class AdvancedBotStats(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bot.start_time = datetime.datetime.utcnow()
        self.metrics_cache = MetricsCache()
        self.executor = ThreadPoolExecutor(max_workers=2)
        self.bg_task = None
        self.lock = asyncio.Lock()
        
    async def cog_load(self):
        self.bg_task = self.bot.loop.create_task(self.metrics_collector())
        
    async def cog_unload(self):
        if self.bg_task:
            self.bg_task.cancel()
        self.executor.shutdown(wait=False)
        
    async def metrics_collector(self):
        while True:
            await self.update_metrics()
            await asyncio.sleep(60)
            
    def get_system_metrics(self) -> SystemMetrics:
        net_io = psutil.net_io_counters()
        return SystemMetrics(
            cpu_percent=psutil.cpu_percent(interval=None),
            memory_percent=psutil.virtual_memory().percent,
            disk_percent=psutil.disk_usage('/').percent,
            network_sent=net_io.bytes_sent,
            network_recv=net_io.bytes_recv,
            process_threads=len(psutil.Process().threads())
        )
        
    async def update_metrics(self):
        async with self.lock:
            metrics = await self.bot.loop.run_in_executor(
                self.executor,
                self.get_system_metrics
            )
            self.metrics_cache.add_metrics(metrics)
            
    async def generate_embed(self, category: str) -> discord.Embed:
        if not self.metrics_cache.metrics:
            return discord.Embed(title="No metrics available", description="Please wait for metrics collection...")
            
        latest = self.metrics_cache.metrics[-1]
        
        if category == "system":
            embed = discord.Embed(
                title="🖥️ System Statistics",
                color=discord.Color.blue(),
                timestamp=datetime.datetime.utcnow()
            )
            embed.add_field(
                name="CPU Usage",
                value=f"```\nCurrent: {latest.cpu_percent:.1f}%\n5min avg: {self.metrics_cache.get_average('cpu_percent'):.1f}%```",
                inline=True
            )
            embed.add_field(
                name="Memory Usage",
                value=f"```\nCurrent: {latest.memory_percent:.1f}%\n5min avg: {self.metrics_cache.get_average('memory_percent'):.1f}%```",
                inline=True
            )
            embed.add_field(
                name="Disk Usage",
                value=f"```\nCurrent: {latest.disk_percent:.1f}%\n5min avg: {self.metrics_cache.get_average('disk_percent'):.1f}%```",
                inline=True
            )
            
        elif category == "bot":
            embed = discord.Embed(
                title="🤖 Bot Statistics",
                color=discord.Color.green(),
                timestamp=datetime.datetime.utcnow()
            )
            uptime = datetime.datetime.utcnow() - self.bot.start_time
            embed.add_field(name="Uptime", value=f"```\n{humanize.precisedelta(uptime)}```", inline=False)
            embed.add_field(name="Servers", value=f"```\n{len(self.bot.guilds):,}```", inline=True)
            embed.add_field(name="Users", value=f"```\n{len(self.bot.users):,}```", inline=True)
            embed.add_field(name="Channels", value=f"```\n{sum(len(g.channels) for g in self.bot.guilds):,}```", inline=True)
            embed.add_field(name="Commands", value=f"```\n{len(self.bot.commands):,}```", inline=True)
            embed.add_field(name="Threads", value=f"```\n{latest.process_threads}```", inline=True)
            
        elif category == "network":
            embed = discord.Embed(
                title="🌐 Network Statistics",
                color=discord.Color.purple(),
                timestamp=datetime.datetime.utcnow()
            )
            embed.add_field(
                name="Data Sent",
                value=f"```\n{humanize.naturalsize(latest.network_sent)}```",
                inline=True
            )
            embed.add_field(
                name="Data Received",
                value=f"```\n{humanize.naturalsize(latest.network_recv)}```",
                inline=True
            )
            
        elif category == "charts":
            embed = discord.Embed(
                title="📊 Resource Usage Charts",
                color=discord.Color.gold(),
                timestamp=datetime.datetime.utcnow()
            )
            embed.set_footer(text="Last 60 minutes of metrics")
            
        return embed

    @commands.command(name='stats')
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def show_stats(self, ctx: commands.Context):
        """Display advanced bot statistics with real-time updates and charts"""
        try:
            async with ctx.typing():
                await self.update_metrics()
                embed = await self.generate_embed("system")
                view = StatsView(self)
                await ctx.send(embed=embed, view=view)
        except Exception as e:
            await ctx.send(f"⚠️ Error: {str(e)}")

async def setup(bot):
    await bot.add_cog(AdvancedBotStats(bot))