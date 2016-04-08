from gi.repository import GLib
from xml.etree import ElementTree as ET
from .auto_names import *

try:
	from inspect import Signature, Parameter
	put_signature_in_doc = False
except:
	from ._inspect3 import Signature, Parameter
	put_signature_in_doc = True

class ProxyMixin(object):
	__slots__ = ()

	def get(self, bus_name, object_path=None):
		"""Get a remote object.

		Parameters
		----------
		bus_name : string
			Name of the service that exposes this object.
			You may start with "." - then org.freedesktop will be automatically prepended.
		object_path : string, optional
			Path of the object. If not provided, bus_name translated to path format is used.

		Returns
		-------
		ProxyObject implementing all the Interfaces exposed by the remote object.
		Note that it inherits from multiple Interfaces, so the method you want to use
		may be shadowed by another one, eg. from a newer version of the interface.
		Therefore, to interact with only a single interface, use:
		>>> bus.get("org.freedesktop.systemd1")["org.freedesktop.systemd1.Manager"]
		or simply
		>>> bus.get(".systemd1")[".Manager"]
		which will give you access to the one specific interface.
		"""
		bus_name = auto_bus_name(bus_name)
		object_path = auto_object_path(bus_name, object_path)

		xml = self.con.call_sync(
			bus_name, object_path,
			'org.freedesktop.DBus.Introspectable', "Introspect", None, GLib.VariantType.new("(s)"),
			0, self.timeout, None).unpack()[0]

		introspection = ET.fromstring(xml)

		if len(introspection) == 0:
			raise KeyError("no such object")

		return CompositeInterface(introspection)(self, bus_name, object_path)

class ProxyObject(object):
	def __init__(self, bus, bus_name, path):
		self._bus = bus
		self._bus_name = bus_name
		self._path = path

class Signal(object):
	def __init__(self, iface, signal, args):
		self.iface = iface
		self.signal = signal
		self.args = args
		self.__name__ = signal
		self.__qualname__ = iface + "." + self.__name__
		self.__doc__ = "Signal. Callback: (" + ", ".join(args) + ")"

	def connect(self, object, callback):
		"""Subscribe to the signal."""
		def signal_fired(sender, object, iface, signal, params):
			callback(*params)
		return object._bus.subscribe(sender=object._bus_name, object=object._path, iface=self.iface, signal=self.signal, signal_fired=signal_fired)

	def __get__(self, instance, owner):
		if instance is None:
			return self

		class BoundSignal(object):
			__slots__ = ("object", "signal")
			__qualname__ = __name__ = self.iface + "." + self.signal
			__module__ = "DBUS"

			def __init__(self, object, signal):
				self.object = object
				self.signal = signal

			def connect(self, callback):
				return self.signal.connect(self.object, callback)
			connect.__doc__ = "Subscribe to the signal. Callback: (" + ", ".join(self.args) + ")"

			def __repr__(self):
				return "<bound signal " + self.signal.__qualname__ + " of " + repr(self.object) + ">"

		return BoundSignal(instance, self)

	def __set__(self, instance, value):
		raise AttributeError("can't set attribute")

	def __repr__(self):
		return "<signal " + self.__qualname__ + " at 0x" + format(id(self), "x") + ">"

class OnSignal(object):
	def __init__(self, signal):
		self.signal = signal
		self.__name__ = "on" + signal.signal
		self.__qualname__ = signal.iface + "." + self.__name__
		self.__doc__ = "Assign a callback to subscribe to the signal. Assing None to unsubscribe. Callback: (" + ", ".join(signal.args) + ")"

	def __get__(self, instance, owner):
		if instance is None:
			return self

		try:
			return getattr(instance, "_on" + self.signal.signal)
		except AttributeError:
			return None

	def __set__(self, instance, value):
		if instance is None:
			raise AttributeError("can't set attribute")

		try:
			old = getattr(instance, "_sub" + self.signal.signal)
			old.unsubscribe()
		except AttributeError:
			pass

		if value is None:
			delattr(instance, "_on" + self.signal.signal)
			delattr(instance, "_sub" + self.signal.signal)
			return

		sub = self.signal.connect(instance, value)
		setattr(instance, "_on" + self.signal.signal, value)
		setattr(instance, "_sub" + self.signal.signal, sub)

	def __repr__(self):
		return "<descriptor " + self.__qualname__ + " at 0x" + format(id(self), "x") + ">"

class Property(object):
	def __init__(self, iface_name, prop_name, prop_type, access):
		self._iface_name = iface_name
		self._type = prop_type
		self._readable = access.startswith("read")
		self._writeable = access.endswith("write")
		self.__name__ = prop_name
		self.__qualname__ = iface_name + "." + self.__name__
		self.__doc__ = "(" + prop_type + ") " + access

	def __get__(self, instance, owner):
		if instance is None:
			return self

		if not self._readable:
			raise AttributeError("unreadable attribute")

		return instance["org.freedesktop.DBus.Properties"].Get(self._iface_name, self.__name__)

	def __set__(self, instance, value):
		if instance is None or not self._writeable:
			raise AttributeError("can't set attribute")

		instance["org.freedesktop.DBus.Properties"].Set(self._iface_name, self.__name__, GLib.Variant(self._type, value))

	def __repr__(self):
		return "<property " + self.__qualname__ + " at 0x" + format(id(self), "x") + ">"

class DBUSSignature(Signature):

	def __str__(self):
		result = []
		for param in self.parameters.values():
			p = param.name if not param.name.startswith("arg") else ""
			if type(param.annotation) == str:
				p += ":" + param.annotation
			result.append(p)

		rendered = '({})'.format(', '.join(result))

		if self.return_annotation is not Signature.empty:
			rendered += ' -> {}'.format(self.return_annotation)

		return rendered

bound_method = type(ProxyMixin().get) # TODO find a prettier way to get this type

class ProxyMethod(object):
	def __get__(self, instance, owner):
		if instance is None:
			return self

		return bound_method(self, instance)

	def __call__(self, instance, *args):
		ret = instance._bus.con.call_sync(
			instance._bus_name, instance._path,
			self._iface_name, self.__name__, GLib.Variant(self._sinargs, args), GLib.VariantType.new(self._soutargs),
			0, instance._bus.timeout, None).unpack()

		if len(self._outargs) == 0:
			return None
		elif len(self._outargs) == 1:
			return ret[0]
		else:
			return ret

	def __init__(self, iface_name, method):
		self._iface_name = iface_name
		self.__name__ = method.attrib["name"]
		self.__qualname__ = iface_name + "." + self.__name__
		self.__module__ = "DBUS"

		inargs  = [(arg.attrib.get("name", ""), arg.attrib["type"]) for arg in method if arg.tag == "arg" and arg.attrib["direction"] == "in"]
		self._outargs = [arg.attrib["type"] for arg in method if arg.tag == "arg" and arg.attrib["direction"] == "out"]
		self._sinargs  = "(" + "".join(x[0] for x in inargs) + ")"
		self._soutargs = "(" + "".join(self._outargs) + ")"

		self_param = Parameter("self", Parameter.POSITIONAL_ONLY)
		pos_params = [Parameter(a[0] if a[0] else "arg" + str(i), Parameter.POSITIONAL_ONLY, annotation=a[1]) for i, a in enumerate(inargs)]
		ret_type = Signature.empty if len(self._outargs) == 0 else self._outargs[0] if len(self._outargs) == 1 else "(" + ", ".join(self._outargs) + ")"

		self.__signature__ = DBUSSignature([self_param] + pos_params, return_annotation=ret_type)

		if put_signature_in_doc:
			self.__doc__ = self.__name__ + str(self.__signature__)

	def __repr__(self):
		return "<function " + self.__qualname__ + " at 0x" + format(id(self), "x") + ">"

def Interface(iface):

	class interface(ProxyObject):
		@staticmethod
		def _Introspect():
			print(iface.attrib["name"] + ":")
			for member in iface:
				print("\t" + member.tag + " " + member.attrib["name"])
			print()

	interface.__qualname__ = interface.__name__ = iface.attrib["name"]
	interface.__module__ = "DBUS"

	for member in iface:
		if member.tag == "method":
			setattr(interface, member.attrib["name"], ProxyMethod(iface.attrib["name"], member))
		elif member.tag == "signal":
			signal = Signal(iface.attrib["name"], member.attrib["name"], [arg.attrib["type"] for arg in member if arg.tag == "arg"])
			setattr(interface, member.attrib["name"], signal)
			setattr(interface, "on" + member.attrib["name"], OnSignal(signal))
		elif member.tag == "property":
			setattr(interface, member.attrib["name"],
					Property(iface.attrib["name"], member.attrib["name"], member.attrib["type"], member.attrib["access"]))

	return interface

def CompositeInterface(introspection):
	class CompositeObject(ProxyObject):
		def __getitem__(self, iface):
			if iface == "" or iface[0] == ".":
				iface = self._path.replace("/", ".")[1:] + iface
			matching_bases = [base for base in type(self).__bases__ if base.__name__ == iface]

			if len(matching_bases) == 0:
				raise KeyError(iface)
			assert(len(matching_bases) == 1)

			iface_class = matching_bases[0]
			return iface_class(self._bus, self._bus_name, self._path)

		@classmethod
		def _Introspect(cls):
			for iface in cls.__bases__:
				try:
					iface._Introspect()
				except:
					pass

	ifaces = sorted([x for x in introspection if x.tag == "interface"], key=lambda x: int(x.attrib["name"].startswith("org.freedesktop.DBus.")))
	CompositeObject.__bases__ = tuple(Interface(iface) for iface in ifaces)
	CompositeObject.__name__ = "<CompositeObject>"
	CompositeObject.__qualname__ = "<CompositeObject>(" + "+".join(x.__name__ for x in CompositeObject.__bases__) + ")"
	CompositeObject.__module__ = "DBUS"
	return CompositeObject
