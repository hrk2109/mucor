#!/usr/bin/env python
#
# mucor.py
# (c) James S Blachly, MD 2013
# 

# let print() be a function rather than statement
# ala python3
from __future__ import print_function

import os
import sys
import time
import getopt
import csv
import itertools
import HTSeq
from collections import defaultdict
import gzip
import cPickle as pickle
import pdb

import xml.etree.ElementTree as ET

class Info:
	'''Program info: logo, version, and usage'''
	logo = """
 __    __     __  __     ______     ______     ______    
/\ "-./  \   /\ \/\ \   /\  ___\   /\  __ \   /\  == \   
\ \ \-./\ \  \ \ \_\ \  \ \ \____  \ \ \/\ \  \ \  __<   
 \ \_\ \ \_\  \ \_____\  \ \_____\  \ \_____\  \ \_\ \_\ 
  \/_/  \/_/   \/_____/   \/_____/   \/_____/   \/_/ /_/ 
                                  
"""
	version = "0.16"
	versionInfo = "mucor version {0}\nJames S Blachly, MD\nKarl W Kroll, BS".format(version)
	usage = """
Usage:
{0} [-h] | -g featurefile.gff -f feature_type [-u] -o <output_dir> <mutect001.txt mutect002.txt ... mutectNNN.txt>

Flags:
	-h	Show this help

	-g	GFF3/GTF file describing the features into which mutations will be binned

	-f	String to describe the type of feature to bin for this run.
		e.g. gene_id or transcript_id or chromosome_id

	-u,
	--union
		Join all items with same ID for feature_type (specified by -f)
		into a single, continuous bin. For example, if you want intronic
		variants counted with a gene, use this option. 
		** TO DO **
		WARNING, this will likely lead to spurious results due to things
		like MIR4283-2 which exists twice on + and - strand of the same
		chromosome over 1 megabase apart. This creates one huge spurious
		bin.

	-o	Specify output directory

Output directory:
	Output files will be placed in the specified directory.
	If the directory already exists, an error will occur (won't overwrite)

	Output consists of CSV spreadsheets (.txt):
		1. Plain text report of # variants binned according to feature_type
		2. Summary of pre-filter and post-filter variants
		3. Detailed report of all variants by feature_type

Input files:
	<mutect001.txt mutect002.txt ... mutectNNN.txt>
	Final arguments should be a list of mutations in muTect output format

"""

class MucorFilters(object):
	"""docstring for MucorFilters"""
	def __init__(self):
		super(MucorFilters, self).__init__()
		self._filterSets = None					# internally use dict
		self._filters = None					# internally use dict

	def loadFromXML(self, rootNode):
		'''Load filters and filterSets from mucor XML config file.
		Overwrites any existing filters or filterSets stored in this object.'''

		# namespace
		ns = "{urn:osuccc:hcrg:mucor}"

		if not isinstance(rootNode, ET.Element):
			raise TypeError
		if root.tag != ns + "mucor":
			raise ValueError

		fNode = root.find(ns + "filters")
		fsNode = root.find(ns + "filterSets")
		if fNode == None or fsNode == None:
			raise ValueError

		self._filters = dict()
		self._filterSets = dict()

		for mcFilter in fNode:		# 'filter' is a reserved keyword
			if mcFilter.tag != ns + 'filter':
				raise ValueError
			if 'id' not in mcFilter.attrib.keys():
				raise ValueError

			# add empty string entry to the _filters dictionary
			filterName = mcFilter.attrib['id']
			self._filters[filterName] = ""

			# build the filter expression
			fieldId = mcFilter.find(ns + 'fieldId')
			comparison = mcFilter.find(ns + 'comparison')
			value = mcFilter.find(ns + 'value')

			if fieldId == None or comparison == None or value == None:
				raise ValueError
			if fieldId.text == None or comparison.text == None or value.text == None:
				raise ValueError

			comparison_dict = {
				'equal' : '==',
				'notequal' : '!=',
				'lessthan' : '<',
				'lessorequal' : '<=',
				'greaterthan' : '>',
				'greaterorequal' : '>='
			}

			filter_expr = '( row.' + fieldId.text + ' ' \
						+ comparison_dict[comparison.text] + " '" \
						+ value.text + "' )"

			self._filters[filterName] = filter_expr

		for filterSet in fsNode:
			if filterSet.tag != ns + 'filterSet':
				raise ValueError
			if 'id' not in filterSet.attrib.keys():
				raise ValueError

			# add empty list entry to the _filterSets dictionary
			filterSetName = filterSet.attrib['id']
			self._filterSets[filterSetName] = list()

			# populate the list
			for filterId in filterSet.findall(ns + 'filterId'):	# if no filterId tags found, this is ok, no error (an empty filterSet)
				if filterId.text == None:						# however if a filterId is not specified, this is malformed XML
					raise ValueError

				filterName = filterId.text
				filterExpr = self.filter(filterName)
				assert filterExpr != None

				self._filterSets[filterSetName].append(filterExpr)


	def filter(self, filterName):
		'''Return the python expr (as string) corresponding to filterName, or None if filterName not in the _filters dictionary'''
		if self._filters == None: return None
		elif filterName not in self._filters.keys():
			return None
		else:
			return self._filters[filterName]	# return the python expr as string

	@property
	def filters(self):
		'''Return a list of filterIds (names)'''
		if self._filters == None: return None
		else:
			return self._filters.keys()

	@property
	def filterSets(self):
		'''Return a list of filterSet ids (names)'''
		if self._filterSets == None: return None
		else:
			return self._filterSets.keys()

	def filtersInSet(self, filterSetName):
		'''Return a list of python evaluable statements representing the filters in the given filterSet'''
		if self._filterSets == None: return None
		if filterSetName not in self._filterSets.keys(): return None

		return self._filterSets[filterSetName]

class Variant:
	'''Data about SNV (and Indels - TO DO)'''
	def __init__(self,source,pos,ref,alt,frac,dp, eff):
		self.source = source	# source of variant - typically a filename
		self.pos = pos			# HTSeq.GenomicPosition
		self.ref = ref
		self.alt = alt
		self.frac = frac        ######## Karl Added ##############
		self.dp = dp            ######## Karl Added ##############
		self.eff = eff          ######## Karl Added ##############
		#self.annot = annot      ######## Karl Added ##############

class MucorFeature(HTSeq.GenomicFeature):
	'''Specific Genomic Feature. For example, gene SF3B1, band 13q13.1, or chromosome X'''

	def __init__(self, name, type_, interval):
		if name == '': raise NameError('name was an empty string')
		if type_ == '': raise NameError('type_ was an empty string')
		if not isinstance(interval, HTSeq.GenomicInterval): raise TypeError('interval must be of type HTSeq.GenomicInterval')
		self.variants = set()					# empty set to be filled with objects of class Variant
		HTSeq.GenomicFeature.__init__(self, name, type_, interval)
	
	def numVariants(self):
		return len(self.variants)

	def weightedVariants(self):
		'''Instead of returning the number of variants, return the sum of tumor_f for all variants'''
		tumor_f_sum = 0.0
		for var in self.variants:
			tumor_f_sum += float(var.frac)

		return tumor_f_sum

	def uniqueVariants(self):
		'''Return the set of unique variants from the set of all variants (for this feature)'''
		# exploit the hashtable and uniqueness of sets to quickly find
		# unique tuples (contig, pos, ref, alt) of variant info
		# sorted by chrom, pos
		uniqueVariantsTemp = set()
		for var in self.variants:
			candidate = (var.pos.chrom, var.pos.pos, var.ref, var.alt)
			uniqueVariantsTemp.add(candidate)
		# sort by chr, then position
		# TO DO: python sorted() will sort as: chr1, chr10, chr2, chr20, chrX. Fix.
		uniqueVariantsTemp = sorted(uniqueVariantsTemp, key=lambda varx: ( varx[0] + str(varx[1]) ) )

		# Now construct a returnable set of Variant objects,
		# specifying multiple "sources" in the source field
		# this loop's inner-product is #unique variants * #total variants, times #features
		# and is a major inefficiency
		uniqueVariants = set()
		for uniqueVarTup in uniqueVariantsTemp:
			source = ""
			frac = ""   ######## Karl Added ##############
			dp = ""     ######## Karl Added ##############
			eff = ""
			#annot = ""
			for varClass in self.variants:
				if (varClass.pos.chrom, varClass.pos.pos, varClass.ref, varClass.alt) == uniqueVarTup:
					source += varClass.source + ", "
					frac += str(varClass.frac) + ", "   ######## Karl Added ##############
					dp += str(varClass.dp) + ", "       ######## Karl Added ##############
					eff += str(varClass.eff) + ", "     ######## Karl Added ##############
					#annot += str(varClass.annot) + ", " ######## Karl Added ##############
			pos = HTSeq.GenomicPosition(uniqueVarTup[0], uniqueVarTup[1] )
			uniqueVar = Variant(source.strip(", "), pos, ref=uniqueVarTup[2], alt=uniqueVarTup[3], frac=str(frac).strip(", "), dp=str(dp).strip(", "), eff=str(eff).strip(", ")) ######## Karl Modified ##############
			uniqueVariants.add(uniqueVar)

		return uniqueVariants

	def numUniqueVariants(self):
		'''Return the number of unique variants from the set of all variants (for this feature)'''
		return len(self.uniqueVariants())

	def numUniqueSamples(self):
		sources = set()
		for var in self.variants:
			sources.add(var.source)
		return len(sources)

def usage():
	scriptName = sys.argv[0]
	print(Info.usage.format(scriptName))

def abortWithMessage(message, help = False):
	print("*** FATAL ERROR: " + message + " ***")
	if help: usage()
	exit(2)

######## Karl Added ##############   makes a dictionary out of dbSNP, with tuple of chrom,position as the key and the rs number as values.
def load_dbsnp():
	startTime = time.clock()
	snps = defaultdict(str)
	dbsnp_p = '/nfs/17/osu7366/projects/new_AK/dbSNPandMiSeq.P'
	#dbsnp_file = '/nfs/17/osu7366/reference/snp138Common.txt.gz'
	#dbsnp = gzip.open(dbsnp_file,'rb')
	print("\n=== Reading dbSNP pickle file {0} ===".format(dbsnp_p))
	'''
	for line in dbsnp:
		col = line.split('\t')
		if str(col[11]) == "deletion": # deletions in our VCF file start 1 base upstream (-1) from dbSNP, but have the correct rs number
			snps[tuple((str(col[1]), int(col[3]) - 1))] = str(col[4])
		else:
			snps[tuple((str(col[1]), int(col[3])))] = str(col[4])
	'''
	snps = pickle.load(open(dbsnp_p,'rb'))
	totalTime = time.clock() - startTime
	print("{0} sec\t{1} SNPs".format(int(totalTime), len(snps.values())))
	return snps

##################################

######## Karl Added ##############   true or false to check if a location (tuple of chrom,position) is in the dbSNP dictionary. 
#									 must use defaultdict above to avoid key errors here
def in_dbsnp(snps, loc):
	status = False
	annotation = snps[loc]
	if str(annotation).startswith('rs'):
		status = True
	return status
##################################

def parseGffFile(gffFileName, featureType):
	'''Parse the GFF/GTF file. Return tuple (knownFeatures, GenomicArrayOfSets)
	Haplotype contigs are explicitly excluded because of a coordinate crash (begin > end)'''
	
	# TO DO: command line flag should indicate that variants in INTRONS are counted
	# This is called --union, see below
	
	startTime = time.clock()
	print("\n=== Reading GFF/GTF file {0} ===".format(gffFileName))
	
	gffFile = HTSeq.GFF_Reader(gffFileName)
	
	#ga = HTSeq.GenomicArray("auto", typecode="i")	# typecode i is integer
	
	knownFeatures = {}								# empty dict

	duplicateFeatures = set()

	# gas - GenomicArrayOfSets.
	# typecode always 'O' (object) for GenomicArrayOfSets
	# UNstranded -- VCF and muTect output always report on + strand,
	# but the GenomicArray must be unstranded because the GFF /is/ strand-specific,
	# and if I manually coded all GenomicIntervals read from the VCF or muTect file as '+',
	# then no genes on the - strand would have variants binned to them
	gas = HTSeq.GenomicArrayOfSets("auto", stranded=False)

	for feature in itertools.islice(gffFile, 0, None):
		# Nonstandard contigs (eg chr17_ctg5_hap1, chr19_gl000209_random, chrUn_...)
		# must be specifically excluded, otherwise you will end up with exception
		# ValueError: start is larger than end due to duplicated gene symbols
		if "_hap" in feature.iv.chrom:
			continue
		elif "_random" in feature.iv.chrom:
			continue
		elif "chrUn_" in feature.iv.chrom:
			continue

		# transform feature to instance of Class MucorFeature
		feat = MucorFeature(feature.attr[featureType], feature.type, feature.iv)
		
		# WARNING
		# the following REQUIRES a coordinate-sorted GFF/GTF file
		# extra checks incurring slowdown penalty are req'd if GFF/GTF not sorted
		if feat.name in knownFeatures:
			# In case there is an error in the GFF and/or the featureType (-f) is not unique,
			# issue a warning
			# for example, genes.gtf supplied with the Illumina igenomes package for the tuxedo tools suite
			# includes duplicate entries for many genes -- e.g. DDX11L1 on chr15 shoudl be DDX11L9
			# try to cope with this by relabeling subsequent genes as GENESYM.chrNN
			if feat.iv.chrom != knownFeatures[feat.name].iv.chrom:
				duplicateFeatures.add(feat.name)
				feat.name = feat.name + '.' + feat.iv.chrom
			else:
				# do not obliterate the start coordinate when adding SUCCESSIVE bits of a feature (e.g. exons)
				# TO DO: Here is where the --nounion option would work
				#feat.iv.start = knownFeatures[feat.name].iv.start
				pass # no-union - this does overwrite previous coordinates in knownFeatures,
					 # but should not matter as the actual coordinates are obtaind from 'gas'.

		# first, add to the knownFeatures, a dictionary of MucorFeatures, which contain the variants set
		knownFeatures[feat.name] = feat
		# then, add to the GenomicArrayOfSets, which we use to find gene symbol from variant coords
		try:
			gas[ feat.iv ] += feat.name
		except ValueError:
			print(feat.name)
			print(feat.iv)
			raise

	if duplicateFeatures:
		print("*** WARNING: {0} {1}'s found on more than one contig".format(len(duplicateFeatures), featureType))

	totalTime = time.clock() - startTime
	print("{0} sec\t{1} found:\t{2}".format(int(totalTime), featureType, len(knownFeatures)))

	return knownFeatures, gas

def parse_MiSeq(row, fieldId, header):
	VF = row[fieldId[header[-1]]].split(':')[-2]
	DP = row[fieldId[header[-1]]].split(':')[2]
	position = int(row[fieldId['POS']])
	return VF, DP, position

def parse_IonTorrent(row, fieldId, header):
	for i in row[fieldId['INFO']].split(';'):
		if i.startswith("AO="):
			tempval = i.split('=')[1]
		if i.startswith("RO="):
			RO = i.split('=')[1]
		if i.startswith("DP="):
			DP = i.split("=")[1]
	if str(',') in str(tempval):
		tempval2 = [int(numeric_string) for numeric_string in tempval.split(',')]
		try:
			AO = sum(tempval2)
		except:
			print("what's up with this? " + str(tempval2) )
			sys.exit(1)
	else:
		AO = tempval
	VF = float(float(AO)/float(float(RO) + float(AO)))
	position = int(row[fieldId['POS']])
	for i in str(row[fieldId['ALT']]).split(','):
		if len(str(row[fieldId['REF']])) > len(i):
			#this is a deletion in Ion Torrent data
			position = int(row[fieldId['POS']])
			break
	return VF, DP, position

def parse_MuTect(row, fieldId, header, fn, MuTect_Annotations):
	j = 0
	for i in header:
		if str('-') in str(i):
			tmpsampID = i
	for i in row[fieldId['FORMAT']].split(':'):
		if i == "FA":
			VF = row[fieldId[tmpsampID]].split(':')[j]
		elif i == "DP":
			DP = row[fieldId[tmpsampID]].split(':')[j]
		j+=1
	position = int(row[fieldId['POS']])

	global MuTect_switch
	MuTect_switch = True
	if MuTect_switch == True:
		MuTect_output = fn.strip('_snpEff.vcf') + '.out'
		for line in open(MuTect_output):
			if str(str(row[0]) + "\t") in str(line) and str(str(position) + "\t") in str(line):
			#try:
				MuTect_Annotations[tuple((str(row[0]), position))] = line.split('\t')[8]
				#pdb.set_trace()
				break
			#except:
			else:
				#print(row)
				#print(line)
				#pdb.set_trace()
				continue
				#print(row[0])
				#print(position) 
				#print(line.split('\t'))
				#continue

			

	return VF, DP, position, MuTect_Annotations

def parse_SomaticIndelDetector(row, fieldId, header):
	j = 0
	for i in header:
		if str('-') in str(i):
			tmpsampID = i
	for i in row[fieldId['FORMAT']].split(':'):
		if i == "AD":
			ALT_count = row[fieldId[tmpsampID]].split(':')[j].split(',')[1]
		elif i == "DP":
			DP = row[fieldId[tmpsampID]].split(':')[j]
			VF = float( float(ALT_count)/float(DP) )
		j+=1
	position = int(row[fieldId['POS']])
	return VF, DP, position


def parseVariantFiles(variantFiles, knownFeatures, gas, snps):
	# parse the variant files (muTect format)
	# TO DO: also interpret from VCF

	startTime = time.clock()
	global MuTect_Annotations
	MuTect_Annotations = defaultdict(str)

	print("\n=== Reading Variant Files ===")
	for fn in variantFiles:
		print("\t{0}\t".format(fn), end='')

		varFile = open(fn, 'rb')	# TO DO: error handling
		varReader = csv.reader(varFile, delimiter='\t')

		# '## muTector v1.0.47986'
		row = varReader.next()
		#if len(row) != 1: raise ValueError('Invalid muTector header')
		#if "## muTector" not in row[0]: raise ValueError('Invalid muTector header')
		global MiSeq
		MiSeq = False
		global IonTorrent
		IonTorrent = False
		global Mutect
		Mutect = False
		global SomaticIndelDetector
		SomaticIndelDetector = False
		while str(row).split("'")[1][0:2] == '##':
			if str('Torrent Unified Variant Caller') in str(row): 
				IonTorrent = True
			elif str('MiSeq') in str(row):
				MiSeq = True
			elif str('SomaticIndelDetector') in str(row):
				SomaticIndelDetector = True
			elif str('MuTect') in str(row):
				Mutect = True     ## Assume that if not IonTorrent or MiSeq variant calls, must be mutect? 
				#global MuTect_Annotations
				#MuTect_Annotations = defaultdict(str)
			row = varReader.next()

		header = row
		if len(header) == 0: raise ValueError('Invalid header')
		fieldId = dict(zip(header, range(0, len(header))))

		# read coverage depth minimum cutoff; currently unusued
		read_depth_min = 0
		# after reading the two header rows, read data
		for row in itertools.islice(varReader, None):

			############## Karl Added / Modified ################
			######### Added variant frequency and depth to the file reading ########

			#############
			## FILTERS ##   
			#############
			#if row[fieldId['FILTER']] != 'PASS': continue
			if row[fieldId['FILTER']] == 'REJECT': continue
			'''
			if MiSeq and str(row[fieldId['ID']])[0:2] == 'rs': 
				chrom = str(row[fieldId['#CHROM']])
				position = str(row[fieldId['POS']])
				print("found annotated mutation " + str(row[fieldId['ID']]) + " not in snp dictionary\n\tadding it now")
				snps[tuple((str(chrom),int(position)))] = str(row[fieldId['ID']])
				continue
			'''
			#if str(row[fieldId['INFO']]).split(';')[3].split('=')[1] >= int(read_depth_min): continue
			
			# make a variant object for row
			# TO DO: change row index#s to column names or transition to row object

			EFF = ""
			muts = []
			#annot = "NA"
			for eff in row[fieldId['INFO']].split(';'):
				if eff.startswith('EFF='):
					for j in eff.split(','):
						muts.append(str(j.split('|')[3]))
			for guy in set(muts):
				if str(guy) != "":
					EFF += str(guy) + ";"

			if MiSeq:
				VF, DP, position = parse_MiSeq(row, fieldId, header)
			elif IonTorrent:
				VF, DP, position = parse_IonTorrent(row, fieldId, header)
			elif Mutect:
				VF, DP, position, MuTect_Annotations = parse_MuTect(row, fieldId, header, fn, MuTect_Annotations)
			elif SomaticIndelDetector:
				VF, DP, position = parse_SomaticIndelDetector(row, fieldId, header)

			else:
				print("This isn't MiSeq, IonTorrent, SomaticIndelDetector, or Mutect data?")
				sys.exit(1)
			var = Variant(source=fn, pos=HTSeq.GenomicPosition(row[0], int(position)), ref=row[3], alt=row[4], frac=VF, dp=DP, eff=EFF.strip(';'))
			###########################################

			# find bin for variant location
			resultSet = gas[ var.pos ]		# returns a set of zero to n IDs (e.g. gene symbols)
			if resultSet:					# which I'll use as a key on the knownFeactures dict
				#print var.pos				# and each feature with matching ID gets allocated the variant
				#print(gas[ var.pos ])		# 
				for featureName in resultSet:
					knownFeatures[featureName].variants.add(var)

		
		totalTime = time.clock() - startTime
		print("{0:02d}:{1:02d}".format(int(totalTime/60), int(totalTime % 60)))

	return knownFeatures, gas, snps

def printOutput(argv, outputDirName, knownFeatures, gas, snps): ######## Karl Modified ##############
	'''Output statistics and variant details to the specified output directory.'''

	startTime = time.clock()
	print("\n=== Writing output files to {0}/ ===".format(outputDirName))

	try:
		# of = outputFile
		ofRunInfo = open(outputDirName + "/run_info.txt", 'w+')
		ofCounts = open(outputDirName + "/counts.txt", 'w+')
		ofVariantDetails = open(outputDirName + "/variant_details.txt", 'w+')
		ofVariantBeds = open(outputDirName + "/variant_locations.bed", 'w+')
	except:
		abortWithMessage("Error opening output files in {0}/".format(outputDirName))

	# =========================
	# run_info.txt
	#
	ofRunInfo.write(Info.versionInfo + '\n')
	ofRunInfo.write("{0}\n".format(time.ctime() ) )
	ofRunInfo.write("Command line: {0}\n".format(str(argv) ) )
	ofRunInfo.write("No. samples: \n")
	ofRunInfo.write("Filters:\n")
	#
	ofRunInfo.write("Variants Pre-filter: \n")
	ofRunInfo.write("        Post-filter: \n")
	ofRunInfo.close()

	# ============================================================
	# counts.txt
	# make master list, then sort it by number of variants per bin
	#
	ofCounts.write('FeatureName\tHits\tWeightedHits\tAverageWeight\tUniqueHits\tNumSamples\n')
	masterList = list(knownFeatures.values())
	sortedList = sorted(masterList, key=lambda k: k.numVariants(), reverse=True)
	nrow = 0

	for feature in sortedList:
		if knownFeatures[feature.name].variants:
			ofCounts.write(feature.name + '\t')

			ofCounts.write(str(len(knownFeatures[feature.name].variants)) + '\t')
			
			ofCounts.write(str(knownFeatures[feature.name].weightedVariants()) + '\t')
			
			ft = knownFeatures[feature.name]
			avgWt = float(ft.weightedVariants() / float(ft.numVariants()) )
			ofCounts.write(str(avgWt) + '\t')

			ofCounts.write(str(knownFeatures[feature.name].numUniqueVariants()) + '\t')
			
			ofCounts.write(str(knownFeatures[feature.name].numUniqueSamples()) + '\n')

			nrow += 1
		'''
		else:
			print(feature)
		'''
	
	print("\t{0}: {1} rows".format(ofCounts.name, nrow))
	ofCounts.close()
	totalTime = time.clock() - startTime
	print("\tTime to write: {0:02d}:{1:02d}".format(int(totalTime/60), int(totalTime % 60)))

	# =========================================================
	# variant_details.txt
	#
	ofVariantDetails.write('Feature\tContig\tPos\tRef\tAlt\tVF\tDP\tEffect\tSource\t') ######## Karl Modified ##############
	if MuTect_switch:
		ofVariantDetails.write('Annotation\tCount\n')
	elif not MuTect_switch:
		ofVariantDetails.write('Count\n')
	for feature in sortedList:
		if knownFeatures[feature.name].variants:
			for var in knownFeatures[feature.name].uniqueVariants():
				if not in_dbsnp(snps, tuple((var.pos.chrom,var.pos.pos))):  ##### KARL ADDED ######
					ofVariantDetails.write(feature.name + '\t')
					ofVariantDetails.write(var.pos.chrom + '\t')
					ofVariantBeds.write(var.pos.chrom + '\t')
					ofVariantDetails.write(str(var.pos.pos) + '\t')
					ofVariantBeds.write(str(var.pos.pos - 1) + '\t')
					ofVariantBeds.write(str(var.pos.pos) + '\t')
					#ofVariantBeds.write(str(snps[tuple((var.pos.chrom,var.pos.pos))]) + '\t')
					ofVariantDetails.write(var.ref + '\t')
					ofVariantDetails.write(var.alt + '\t')
					ofVariantDetails.write(var.frac + '\t')
					ofVariantDetails.write(var.dp + '\t')
					ofVariantDetails.write(var.eff + '\t')
					ofVariantDetails.write(var.source + '\t')
					if MuTect_switch:
						ofVariantDetails.write(MuTect_Annotations[(var.pos.chrom, var.pos.pos)] + '\t')
					ofVariantDetails.write(str(len(var.source.split(','))) + '\n')
					ofVariantBeds.write('\n')
		########### Karl added bed file output here #############
	ofVariantDetails.close()
	ofVariantBeds.close()
	
	return 0


def main(argv):
	print(Info.logo)
	print(Info.versionInfo)

	print("\n=== Run Info ===")
	print("\t{0}".format(time.ctime() ) )
	print("\tCommand line: {0}".format(str(argv) ) )

	# initialize to empty in case not specified
	gffFileName = ""
	featureType = ""
	outputDirName = ""

	try:
		opts, args = getopt.getopt(argv, "hg:f:uo:", ["help", "gff=", "feature_type=", "union", "output="])
	except getopt.GetoptError:
		usage()
		sys.exit(1)
	for opt, arg in opts:
		if opt in ("-h", "--help"):
			usage()
			sys.exit()
		elif opt in ("-g", "--gff"):
			gffFileName = arg
			if not os.path.exists(gffFileName):
				abortWithMessage("Could not find GFF file {0}".format(gffFileName))
		elif opt in ("-f", "--feature_type"):
			featureType = arg
		elif opt in ("-o", "--output"):
			outputDirName = arg
			if os.path.exists(outputDirName):
				abortWithMessage("The directory {0} already exists. Will not overwrite.".format(outputDirName))
			else:
				try:
					os.makedirs(outputDirName)
				except:
					abortWithMessage("Error when creating output directory {0}".format(outputDirName))

	# everything remaining is arguments, i.e. the variant files
	variantFiles = args
	
	# Check that options were correctly specified
	if gffFileName == '':
		abortWithMessage("GFF file was not specified", help = True)
	elif featureType == '':
		abortWithMessage("Feature type was not specified", help = True)
	elif outputDirName == '':
		abortWithMessage("Output directory name not specified", help = True)
	if len(args) == 0:
		# args contains the muTect filenames passed
		abortWithMessage("muTect source files were not specified", help = True)

	# check that all specified variant files exist
	for fn in variantFiles:
		if not os.path.exists(fn):
			abortWithMessage("Could not find variant file: {0}".format(fn))


	knownFeatures, gas = parseGffFile(gffFileName, featureType)

	snps =  defaultdict(tuple) # load_dbsnp() ######## Karl Added ##############

	knownFeatures, gas, snps = parseVariantFiles(variantFiles, knownFeatures, gas, snps)

	printOutput(argv, outputDirName, knownFeatures, gas, snps) ######## Karl Modified ##############

	# pretty print newline before exit
	print()


if __name__ == "__main__":
	if sys.hexversion < 0x02070000:
		raise RuntimeWarning("mucor should be run on python 2.7.0 or greater.")
	main(sys.argv[1:])
