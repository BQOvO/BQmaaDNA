from maa.agent.agent_server import AgentServer
from agent.custom.action.MacroPlayer import MacroPlayer
from agent.custom.action.MacroRecorder import MacroRecorderStart, MacroRecorderStop
from agent.custom.action.Count import Count, CountReset, CountPrint, CountCleanup, CountAdd, CountCheck, CountSet
from agent.custom.action.Looper import Looper
from agent.custom.action.randomr import randomr
from agent.custom.action.FishFight import FishFight
from agent.custom.action.FishFightDebug import FishFightDebug
from agent.custom.action.VoyageClick import VoyageClick
from agent.custom.action.outnoder import Outnoder
from agent.custom.action.NumberComparator import NumberComparator
from agent.custom.action.Timeout import TimeoutStart, TimeoutReset, CheckTimeout
from agent.custom.action.RoundTracker import RoundTracker
from agent.custom.action.TextWatcher import TextWatcher

from agent.custom.sink.aspect_ratio import AspectRatioChecker
from agent.custom.sink.count_cleanup import CountAutoCleanup
from agent.custom.sink.screenshot_on_fail import NodeScreenshotSink
from agent.custom.sink.focus_prefix import FocusPrefix
from agent.custom.sink.option_validation import OptionValidationSink
#from agent.custom.sink.game_health_override import GameHealthOverride   （暂时不用先）

from agent.custom.recongition.CheckResolution import CheckResolution
from agent.custom.recongition.GameProcessCheck import GameProcessCheck


def _register(name, base_cls, decorator):
    """注册自定义组件的工厂函数，消除重复样板代码"""
    wrapper_cls = type(f"{base_cls.__name__}_Registered", (base_cls,), {})
    decorator(name)(wrapper_cls)
    print(f"[CustomFile] {name} 已注册")


_register("Count", Count, AgentServer.custom_action)
_register("CountReset", CountReset, AgentServer.custom_action)
_register("CountPrint", CountPrint, AgentServer.custom_action)
_register("CountCleanup", CountCleanup, AgentServer.custom_action)
_register("CountAdd", CountAdd, AgentServer.custom_action)
_register("CountCheck", CountCheck, AgentServer.custom_action)
_register("CountSet", CountSet, AgentServer.custom_action)
_register("MacroPlayer", MacroPlayer, AgentServer.custom_action)

_register("MacroRecorderStart", MacroRecorderStart, AgentServer.custom_action)
_register("MacroRecorderStop", MacroRecorderStop, AgentServer.custom_action)
_register("Looper", Looper, AgentServer.custom_action)
_register("randomr", randomr, AgentServer.custom_action)
_register("FishFight", FishFight, AgentServer.custom_action)
_register("FishFightDebug", FishFightDebug, AgentServer.custom_action)
_register("VoyageClick", VoyageClick, AgentServer.custom_action)
_register("outnoder", Outnoder, AgentServer.custom_action)
_register("NumberComparator", NumberComparator, AgentServer.custom_action)
_register("TimeoutStart", TimeoutStart, AgentServer.custom_action)
_register("TimeoutReset", TimeoutReset, AgentServer.custom_action)
_register("CheckTimeout", CheckTimeout, AgentServer.custom_action)
_register("RoundTracker", RoundTracker, AgentServer.custom_action)
_register("TextWatcher", TextWatcher, AgentServer.custom_action)
_register("CheckResolution", CheckResolution, AgentServer.custom_recognition)
_register("GameProcessCheck", GameProcessCheck, AgentServer.custom_recognition)