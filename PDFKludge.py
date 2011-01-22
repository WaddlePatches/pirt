
# PDFKludge v1.0

# Copyright (c) 2011 Ben Klein
# Licensed under the Non-Profit Open Software License version 3.0 except as
# follows:
#  a) The Original Copyright Holder (OCH) of the Original Work reserves the
#    right to re-license or sub-license the Original Work or Derivative Works
#    authored by OCH without limitation; and
#  b) Licensor grants You the right to re-license the Original Work or
#    Derivative Works thereof under the original OSL 3.0 as described in
#    section 17 only if no revenue whatsoever is derived from the distribution
#    of the Original Work or Derivative Works thereof, or from support or
#    services relating thereto.
#  See the included file LICENSE or visit:
#  http://www.opensource.org/licenses/NOSL3.0

# Special thanks to Daniel Klein

import re
import os

try:
	from collections import OrderedDict
except ImportError:
	from ordereddict import OrderedDict

# Regexes used
EOL = "(?:\r\n?|(?<!\r)\n)"	# end of line
SOD = "(?:\r?\n)"	# start of binary data
WS = "(?:[\t ]|" + EOL + ")"	# white space
DLM = "(?:[<>\%[\]/]|" + WS + ")"	# delimiter characters
NAME = "/([^<>\%[\]/ \t\r\n]*)(?=" + DLM + "|$)"	# identifier
# Object references are not handled separately; this is just
# kept for posterity.
REF = "(\d+)" + WS + "(\d+)" + WS + "R"	# object reference

class PDFKludgeError(Exception):
	pass

class PDFKludge(object):
	def __init__(self, infile=None):
		"""Instance variables:
		 * infile:    The current file() object being read. This is set by open_pdf().
		 * xref:      The xref table, stored as a dict(). Key is object number; value is dict of
		              "seek" (seek point), "gen" (generation number as loaded from the table)
			      and "use" (True or False, mapping to PDF values 'n' and 'f' respectively).
		 * seekr:     A dict() mapping seek points to objects. Key is the seek point; value is
		              the object number found at the given seek point, as read from the xref
			      table.
		 * trailer:   The trailer after the first-level xref table, stored as a dict(), as
		              converted by get_dict()."""
		self.infile = infile
		self.xref = {}
		self.seekr = {}
		self.trailer = {}

	@staticmethod
	def get_dict(line):
		"""Recursively convert a PDF formatted dictionary string into a Python dict()
		object. It is expected that the input does NOT have the surrounding << >> PDF
		markers. Returns an OrderedDict."""
		# Kludge to allow sub-call removing stuff from the string
		if isinstance(line, basestring):
			line = {0: line}
		ret = OrderedDict()
		while line[0]:
			r = re.match(WS + "*>>", line[0])
			if r:
				line[0] = line[0][r.end():]
				return ret
			r = re.match(WS + "*" + NAME + WS + "*(..*?)" + WS + "*(?=/|>>|$)", line[0])
			if not r:
				Warn("oops! expected dict item. Got `%s'" % line[0])
				return ret
			name, value = r.groups()
			line[0] = line[0][r.end():]
			# Found a nested dict
			if value == "<<":
				# Do NOT even think about sending line[0]! It must be line
				ret[name] = PDFKludge.get_dict(line)
			# Convert integers for convenience
			elif re.match("\d+$", value):
				ret[name] = int(value)
			# Arrays can be treated as text, but need to be processed separately
			# in case they contain dicts
			elif value.startswith("["):
				while value.count("]") < value.count("["):
					n = line[0].find("]") + 1
					value += line[0][:n]
					line[0] = line[0][n:]
				ret[name] = value
			# Can safely treat hex values and references as strings
			else:
				ret[name] = value
		return ret
	
	@staticmethod
	def dict_to_pdf(d):
		"""Recursively convert a Python dict() object into a PDF formatted dictionary
		string. There is no error-checking done; the dict() is assumed to contain valid
		keys and values. Returns a string."""
		ret = "<< "
		for key, value in d.iteritems():
			ret += "/" + key
			if isinstance(value, dict):
				ret += " " + dict_to_pdf(value) + " "
			else:
				ret += " %s " % value
		ret += ">>"
		return ret

	def get_init_xref(self):
		"""Find and process the current PDF startxref key at the end of self.infile().
		Changes the current seek point. Raises PDFKludgeError or returns True."""
		self.infile.seek(-1024, os.SEEK_END)
		pos = self.infile.tell()
		line = self.infile.read(1024)
		r = line.rfind("startxref")
		# TODO: turn startxref into a function
		if r < 0:
			raise PDFKludgeError("Cannot find startxref after 0x%x" % pos)
		pos += r
		line = line[r + 9:]
		r = re.match(WS + "(\d+)" + WS, line, re.DOTALL)
		if not r:
			raise PDFKludgeError("Bad startxref at 0x%x" % pos)
		# print "startxref at 0x%x" % pos
		return self.get_xref_table(int(r.group(1)))

	def get_xref_table(self, seek_to):
		"""Load in the xref table found at the given seek point. This chain-loads the
		'Prev' xref tables as well, and sets self.trailer. Changes the current seek
		point. Raises PDFKludgeError or returns True."""
		self.infile.seek(seek_to)
		line = self.infile.readline()
		if line.rstrip() != "xref":
			raise PDFKludgeError("Cannot find xref table at 0x%x" % seek_to)

		line = self.infile.readline()
		r = re.match("(\d+)" + WS + "(\d+)" + EOL + "$", line)
		if r == None:
			raise PDFKludgeError("Invalid xref table at 0x%x" % seek_to)

		obj_n = int(r.group(1))
		tab_size = int(r.group(2))

		count = 0
		while count < tab_size:
			pos = self.infile.tell()
			line = self.infile.readline()
			r = re.match("(\d+)" + WS + "(\d{5})" + WS + "([fn])" + WS + "?" + EOL + "$", line)
			if r == None:
				raise PDFKludgeError("Invalid xref at 0x%x" % pos)
			g = int(r.group(1))
			# Don't keep objects with no seek point
			if g != 0 and obj_n not in self.xref:
				self.seekr[g] = {"obj": int(obj_n)}
				self.xref[obj_n] = { "seek": g,
					"gen": int(r.group(2)),
					"use": True if r.group(3) == "n" else False}
			obj_n += 1
			count += 1

		pos = self.infile.tell()
		line = self.infile.readline()
		if not line.startswith("trailer"):
			raise PDFKludgeError("Missing trailer after xref at 0x%x " % pos)
		n = line.find("<<")
		if n < 0:
			line = self.infile.readline()
		else:
			line = line[n:]

		if not line.startswith("<<"):
			raise PDFKludgeError("trailer is not a dict object at 0x%x" % pos)

		# trailer is one-dimensional dict
		while not line.rstrip().endswith(">>"):
			new = self.infile.readline()
			if not new:
				raise PDFKludgeError("EOF reached while reading trailer at 0x%x" % pos)
			line += new
		
		# Convert PDF dict to Python dict() object
		r = re.match("<<(.*)>>", line, re.DOTALL)
		trailer = self.get_dict(r.group(1))
		# Merge the xref tables
		if "Prev" in trailer:
			self.get_xref_table(trailer["Prev"])
			# No longer need to keep previous startxref
			del trailer["Prev"]

		self.trailer = trailer
		return True

	def open_pdf(self, file_name):
		"""Open the given filename and check for a PDF header. Sets self.infile, and will
		close a file previously opened. Raises PDFKludgeError if the file is invalid or
		returns the new self.infile."""
		if self.infile:
			self.infile.close()
			self.infile = None
		self.infile = open(file_name, "r")
		head = self.infile.read(10)
		r = re.match("%PDF-(...)" + EOL, head)
		if not r:
			raise PDFKludgeError("`%s' is not a PDF file" % ifn)
		return self.infile
	
	def get_stream_obj_at(self, from_pt, to_pt=None, head_only=False):
		"""Kludge that reads in an object from self.infile that starts at seek point
		from_pt. If to_pt is not given, it tries to make an intelligent guess as to where
		to stop reading: either the start of the next object stored in the file or, if it
		is the last object, the end of the file. Raises PDFKludgeError or returns a dict()
		of "meta" (the object dictionary) and "data" (the stream that follows the object).
		
		This will only return objects that have streams attached; the purpose of this is
		to locate images with PDFs, and ignore anything else. No error is raised if the
		data at from_pt does not match an object with a stream, regardless of whether it
		is a valid object or not.

		If head_only is True, then the full stream data will not (or may not) be loaded,
		and the returned dict() will only have the "meta" field."""

		obj_stream_match = "((\d+)" + WS + "(\d+)" + WS + "obj" + WS + "?<<(.*)>>" + WS + "?(?<!end)stream" + SOD + ")"
		head_only_size = 4096

		if from_pt not in self.seekr:
			raise PDFKludgeError("asked for object at invalid seek point 0x%x" % from_pt)
		if to_pt == None:
			# Get the next object from the xref table
			keys = sorted(self.seekr.iterkeys())
			k = keys.index(from_pt) + 1
			if k < len(keys):
				to_pt = keys[k]
			# If it was the last object, to_pt will stay None
			# and the entire remainder of the file will be read into memory
		elif to_pt <= from_pt:
			raise PDFKludgeError("asked for object at invalid range: 0x%x to 0x%x" % (from_pt, to_pt))
		# Read the entire object, including stream (and junk) into memory
		self.infile.seek(from_pt)
		data = ""
		if to_pt == None:
			if head_only:
				while not re.match(obj_stream_match, data, re.DOTALL):
					new = self.infile.read(head_only_size)
					if not new:
						break
					data += new
			else:
				data = self.infile.read()
		else:
			read_size = to_pt - from_pt - 1
			if head_only:
				while not re.match(obj_stream_match, data, re.DOTALL) and len(data) < read_size:
					new = self.infile.read(min(read_size, head_only_size))
					read_size = max(read_size - len(new), 0)
					# This condition should never happen unless to_pt > file size
					if not new:
						break
					data += new
			else:
				data = self.infile.read(read_size)
		# Only searching for objects with streams
		# {1: obj_head, 2: obj_no, 3: gen_no, 4: obj_meta}
		r = re.match(obj_stream_match, data, re.DOTALL)
		if r:
			if head_only:
				data = r.group(0)
			obj = self.seekr[from_pt]["obj"]
			if int(r.group(2)) != obj:
				Warn("expected object %d at 0x%x, got %s" % (obj, from_pt, r.group(2)))
				return None
			meta = self.get_dict(r.group(4))
			if "Length" in meta:
				data = data[r.end():r.end() + meta["Length"]]
			else:
				k = re.match("(.*)endstream" + EOL + "endobj" + EOL, data, re.DOTALL)
				if k:
					data = k.group(1)
				else:
					Warn("object %d at 0x%x has wrong stream length or is not terminated" % (obj, from_pt))
			if head_only:
				return {"meta": meta}
			else:
				return {"meta": meta, "data": data}
		#else:
		#	Warn("object at 0x%x is invalid or does not have a stream" % from_pt)
