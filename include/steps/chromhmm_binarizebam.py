import os
from logging import getLogger
from abstract_step import AbstractStep

logger = getLogger('uap_logger')

class ChromHmmBinarizeBam(AbstractStep):
    '''
    This command converts coordinates of aligned reads into binarized data form
    from which a chromatin state model can be learned. The binarization is based
    on a poisson background model. If no control data is specified the parameter
    to the poisson distribution is the global average number of reads per bin.
    If control data is specified the global average number of reads is
    multiplied by the local enrichment for control reads as determined by the
    specified parameters. Optionally intermediate signal files can also be
    outputted and these signal files can later be directly converted into binary
    form using the BinarizeSignal command.
    '''

    def __init__(self, pipeline):
        super(ChromHmmBinarizeBam, self).__init__(pipeline)

        self.set_cores(8)
        
        self.add_connection('in/alignments')
        self.add_connection('out/alignments')
        self.add_connection('out/metrics')
        
        self.require_tool('ChromHMM')
        self.require_tool('echo')
        self.require_tool('ln')

        self.add_option('chrom_sizes_file', str, optional = False,
                        descritpion = "File generated by 'fetchChromSizes'")
        self.add_option('control', dict, optional = False)

        # ChromHMM BinarizeBam Options
        self.add_option('b', int, optional = True)
        self.add_option('c', str, optional = True)
        self.add_option('center', bool, optional = True)
        self.add_option('e', int, optional = True)
        self.add_option('f', int, optional = True)
        self.add_option('g', int, optional = True)
        self.add_option('n', int, optional = True)
        self.add_option('o', str, optional = True)
        self.add_option('p', float, optional = True)
        self.add_option('peaks', bool, optional = True)
        self.add_option('s', int, optional = True)
        self.add_option('strictthresh', bool, optional = True)
        self.add_option('t', str, optional = True)
        self.add_option('u', int, optional = True)
        self.add_option('w', int, optional = True)

    def runs(self, run_ids_connections_files):

        options = ['b', 'c', 'center', 'e', 'f', 'g', 'n', 'o', 'p', 'peaks',
                   's', 'strictthresh', 't', 'u', 'w']

        set_options = [option for option in options if \
                       self.is_option_set_in_config(option)]

        option_list = list()
        for option in set_options:
            if isinstance(self.get_option(option), bool):
                if self.get_option(option):
                    option_list.append('-%s' % option)
                else:
                    option_list.append('-%s' % option)
            else:
                option_list.append('-%s' % option)
                option_list.append(str(self.get_option(option)))


        # We need to create a cell-mark-file table file. Should look something
        # like this:
        #
        # cell1 mark1 cell1_mark1.bed cell1_control.bed
        # cell1 mark2 cell1_mark2.bed cell1_control.bed
        # cell2 mark1 cell2_mark1.bed cell2_control.bed
        # cell2 mark2 cell2_mark2.bed cell2_control.bed
        #
        # The control file is optional!!!

        # How can we get the cell and mark information?
        # Cell = key of self.get_option(control)
        # Mark = value of self.get_option(control)


        control_samples = self.get_option('control')
        for control_id, treatment_list in control_samples.iteritems():
            # Check for existence of control files
            control_files = list()
            if control_id != 'None':
                try:
                    control_files = run_ids_connections_files[control_id]\
                                    ['in/alignments']
                    control_id = "-" + control_id
                except KeyError as e:
                    logger.error("Option 'control':\n"
                                 "No control '%s' found.\n" % control_id)
                    sys.exit(1)

            # Check for existence of treatment files
            for tr in treatment_list:
                treatments = dict()
                try:
                    treatments[tr] = run_ids_connections_files[tr]\
                                     ['in/alignments']
                except KeyError as e:
                    logger.error("Option 'control':\n"
                                 "No treatment '%s' for control '%s' found."
                                 % (tr, control_id) )
                    sys.exit(1)

                # Assemble rund ID
                run_id = "%s%s" % (tr, control_id)

                # Create list of input files
                input_paths = [f for l in [treatments[tr], control_files]\
                               for f in l]

                with self.declare_run(run_id) as run:

                    # temp directory = inputbamdir
                    temp_dir = run.get_output_directory_du_jour_placeholder()

                    # necessary for cell-mark-file table
                    linked_controls = list()
                    linked_treatments = list()
                    
                    # Create links to all input paths in temp_dir
                    with run.new_exec_group() as exec_group:
                        for files, links in [[control_files, linked_controls], \
                                             [treatments[tr], linked_treatments]]:
                            for f in files:
                                f_basename = os.path.basename(f)
                                temp_f = run.add_temporary_file(
                                    suffix = f_basename)
                                ln = [self.get_tool('ln'), '-s', f, temp_f]
                                exec_group.add_command(ln)
                            
                                # Save basename of created link
                                links.append(os.path.basename(temp_f))

                        logger.error("Controls: %s" %
                                     ", ".join(linked_controls))
                        logger.error("Treatments: %s" % 
                                     ", ".join(linked_treatments))
                        
                        # Create the table file
                        cell_mark_file_content = str()
                        for lt in linked_treatments:
                            line = "%s\t%s\t%s" % (control_id, tr, lt)
                            if linked_controls:
                                for lc in linked_controls:
                                    line += "\t%s" % lc
                            cell_mark_file_content += "%s\n" % line
                        logger.error(cell_mark_file_content)
                        echo = [self.get_tool('echo'), cell_mark_file_content]

                        cell_mark_file = run.add_temporary_file(suffix = run_id)
                        exec_group.add_command(echo, stdout_path = cell_mark_file)


                    with run.new_exec_group() as exec_group:
                        chromhmm = [ self.get_tool('ChromHMM'),
                                     'BinarizeBam',
                                     self.get_option('chrom_sizes_file'),
                                     temp_dir,
                                     cell_mark_file,
                                     temp_dir
                                 ]

