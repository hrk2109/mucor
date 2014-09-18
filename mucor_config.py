import os
import argparse
from collections import defaultdict
import csv
import json
import pdb
import codecs

cwd = os.getcwd()

def abortWithMessage(message, help = False):
        print("*** FATAL ERROR: " + message + " ***")
        exit(2)

def DetectDataType(fn):
	varFile = open(fn, 'r')
	varReader = csv.reader(varFile, delimiter='\t')
	row = varReader.next()
	while str(row).split("'")[1][0:2] == '##':
		if str('Torrent Unified Variant Caller') in str(row): 
			return "IonTorrent"
			break
		elif str('MiSeq') in str(row):
			return "MiSeq"
			break
		elif str('SomaticIndelDetector') in str(row):
			return "SomaticIndelDetector"
			break
		elif str('MuTect') in str(row):
			return "Mutect"
			break
		row = varReader.next()
	return "Unknown"

def DetectSnpEffStatus(fn):
	varFile = open(fn, 'r')
	varReader = csv.reader(varFile, delimiter='\t')
	row = varReader.next()
	while str(row).split("'")[1][0:2] == '##':
		if str('SnpEff') in str(row): 
			return True
			break
		row = varReader.next()
	return False

def thing(args, proj_dir):
	json_dict = defaultdict()
	json_dict['run_name'] = str(args.output_directory).split('/')[-1]
	json_dict['gtf'] = str(args.gff)
	json_dict['union'] = bool(args.union)
	json_dict['fast'] = bool(args.no_archive)
	json_dict['feature'] = str(args.featuretype)
	json_dict['filters'] = ['MUTECT-KEEP', 'VCF-PASS']
	json_dict['samples'] = list(dict())
	for id in open(args.samples):
		sid = id.strip()
		if str(sid) == "":
			continue
		else:
			something = defaultdict()
			something['id'] = str(sid)
			something['files'] = list()
			for root, dirs, files in os.walk(proj_dir):
				for i in files:
					if str(sid) in str(i):
						full_path = os.path.join(root, i)
						if str(i).split('.')[-1] == str("vcf"):
							something['files'].append({'type':'vcf', 'path':str(full_path), 'snpeff':DetectSnpEffStatus(full_path)})
						elif str(i).split('.')[-1] == str("out"):
							something['files'].append({'type':'mutect', 'path':str(full_path)})
						else:
							print("unsure of what to do with " + str(full_path))
		json_dict['samples'].append(something)
	return json_dict				

def main():
	parser = argparse.ArgumentParser()
	parser.add_argument("-g", "--gff", required=True, help="Annotation GFF/GTF for feature binning")
	parser.add_argument("-s", "--samples", required=True, help="Text file containing sample names")
	parser.add_argument("-d", "--project_directory", required=False, help="Project root directory, in which to find output")
	parser.add_argument("-f", "--featuretype", required=True, help="Feature type into which to bin [gene]")
	parser.add_argument("-n", "--no_archive", action="store_false", default=True, help="prevent quick load of annotation files")
	parser.add_argument("-u", "--union", action="store_true", help="""
	    Join all items with same ID for feature_type (specified by -f)
	    into a single, continuous bin. For example, if you want intronic
	    variants counted with a gene, use this option. 
	    ** TO DO **
	    WARNING, this will likely lead to spurious results due to things
	    like MIR4283-2 which exists twice on + and - strand of the same
	    chromosome over 1 megabase apart. This creates one huge spurious
	    bin.
	    """)
	parser.add_argument("-jco", "--json_config_output", required=True, help="Name of JSON configuration file")   
	parser.add_argument("-od", "--output_directory", required=True, help="Name of Mucor output directory")

	args = parser.parse_args()

	if not os.path.exists(args.gff):
	    abortWithMessage("Could not find GFF file {0}".format(args.gff))
	    '''
	if os.path.exists(args.output):
		pass
	    #abortWithMessage("The directory {0} already exists. Will not overwrite.".format(args.output))
	else:
	    try:
	        os.makedirs(args.output)
	    except:
	        abortWithMessage("Error when creating output directory {0}".format(outputDirName))
	        '''
	if not args.project_directory or not os.path.exists(args.project_directory):
		print("Project directory not found; using CWD")
		proj_dir = cwd
	else:
		proj_dir = args.project_directory

	json_dict = thing(args, proj_dir)
	#pdb.set_trace()
	if os.path.exists(args.json_config_output):
		abortWithMessage("JSON config file {0} already exists.".format(args.json_config_output))
	output_file = codecs.open(args.json_config_output, "w", encoding="utf-8")
	json.dump(json_dict, output_file, sort_keys=True, indent=4, ensure_ascii=True)


if __name__ == "__main__":
	main()
