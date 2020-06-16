'''
Classes AbstractStep and AbstractSourceStep are defined here.

The class AbstractStep has to be inherited by all processing step classes.
The class AbstractSourceStep has to be inherited by all source step classes.

Processing steps generate output files from input files whereas source steps
only provide output files. Both step types may generates tasks, but only source
steps can introduce files from outside the destination path into the pipeline.
'''

# 1. standard library imports
import sys
from datetime import datetime
import inspect
from logging import getLogger
import os
import pwd
import re
import signal
import socket
import time
import traceback
from shutil import copyfile
from tqdm import tqdm
import multiprocessing
# 2. related third party imports
import yaml
# 3. local application/library specific imports
from uaperrors import UAPError
from connections_collector import ConnectionsCollector
import command as command_info
import misc
import process_pool
import pipeline_info
from run import Run

abs_path = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(abs_path, 'steps'))
sys.path.insert(0, os.path.join(abs_path, 'sources'))
logger = getLogger('uap_logger')


class AbstractStep(object):

    PING_TIMEOUT = 300
    PING_RENEW = 30
    VOLATILE_SUFFIX = '.volatile.placeholder.yaml'
    UNDERSCORE_OPTIONS = [
        '_depends',
        '_volatile',
        '_BREAK',
        '_connect',
        '_cluster_submit_options',
        '_cluster_pre_job_command',
        '_cluster_post_job_command',
        '_cluster_job_quota']

    states = misc.Enum(['DEFAULT', 'EXECUTING'])

    def __init__(self, pipeline):

        self._pipeline = pipeline

        self.dependencies = list()
        '''
        All steps this step depends on.
        '''

        self._options = dict()
        '''
        Options as specified in the configuration.
        '''

        self._step_name = self.__module__
        '''
        By default, this is the name of the module. Can be overridden
        to allow for multiple steps of the same kind.
        '''

        self._runs = None
        '''
        Cached run information. ``declare_runs`` is only called once, the
        post-processed run objects are stored in here.
        '''

        self._pipeline_log = dict()

        self._cores = 1
        self._connections = set()
        self._optional_connections = set()
        self._connection_formats = dict()
        self._connection_descriptions = dict()
        self._pre_command = dict()
        self._post_command = dict()
        self._module_load = dict()
        self._module_unload = dict()
        self._tools = dict()

        self._defined_options = dict()

        self.needs_parents = False

        self.children_step_names = set()

        self.finalized = False

        self._state = AbstractStep.states.DEFAULT

        self._submit_script = None

    def finalize(self):
        '''Finalizes the step.

        The intention is to make further changes to the step
        impossible, but apparently, it's checked nowhere at the moment.
        '''
        if self.finalized:
            return

        for parent_step in self.dependencies:
            parent_step.finalize()

        self.finalized = True

    def _reset(self):
        self._pipeline_log = dict()

    def get_pipeline(self):
        return self._pipeline

    def declare_run(self, run_id):
        '''
        Declare a run. Use it like this::

            with self.declare_run(run_id) as run:
                # add output files and information to the run here
        '''
        # Replace whitespaces by underscores
        run_id = re.sub(r'\s', '_', run_id)
        if run_id in self._runs:
            raise UAPError(
                "Cannot declare the same run ID twice: %s." % run_id)
        run = Run(self, run_id)
        self.add_run(run)
        return run

    def add_run(self, run):
        self._runs[run.get_run_id()] = run

    def get_run(self, run_id):
        '''
        Returns a single run object for run_id or None.
        '''
        if run_id in self._runs:
            return self._runs[run_id]
        else:
            return None

    def set_step_name(self, step_name):
        '''
        Change the step name.

        The step name is initially set to the module name. This method
        is used in case we need multiple steps of the same kind.
        '''
        self._step_name = step_name

    def set_options(self, options):
        '''
        Checks and stores step options.

        The options are either set to values given in YAML config or
        the default values set in self.add_option().
        '''
        self._options = dict()

        # set options
        for key, value in options.items():
            if key[0] == '_':
                if key not in AbstractStep.UNDERSCORE_OPTIONS:
                    raise UAPError(
                        "Invalid option in %s: %s" % (key, self))
                self._options[key] = value
            else:
                if key not in self._defined_options:
                    message = "Unknown option in %s (%s): %s." % \
                        (self.get_step_name(), self.get_step_type(), key)
                    logger.error(message + "\nAvailable options are:\n%s" %
                                 yaml.dump(self._defined_options,
                                           Dumper=misc.UAPDumper))
                    raise UAPError(message)
                if value is not None and type(
                        value) not in self._defined_options[key]['types']:
                    raise UAPError(
                        "Invalid type for option %s - it's %s and should be "
                        "one of %s." % (key, type(value),
                                        self._defined_options[key]['types']))
                if self._defined_options[key]['choices'] is not None and \
                   value not in self._defined_options[key]['choices']:
                    raise UAPError(
                        "Invalid value '%s' specified for option %s - "
                        "possible values are %s." %
                        (value, key, self._defined_options[key]['choices']))
                self._options[key] = value

        # set default values for unset options and make sure all required
        # options have been set
        for key, info in self._defined_options.items():
            if key not in self._options:
                if info['optional'] is not True:
                    raise UAPError(
                        "Required option not set in %s: %s." % (self, key))
                self._options[key] = info['default']

        self._options.setdefault('_volatile', False)

        for i in ['_cluster_submit_options', '_cluster_pre_job_command',
                  '_cluster_post_job_command']:
            self._options.setdefault(i, '')
        self._options.setdefault('_cluster_job_quota', 0)

        self._options.setdefault('_connect', dict())
        self._options.setdefault('_depends', list())
        if not isinstance(self._options['_depends'], list):
            self._options['_depends'] = [self._options['_depends']]
        # add implied dependencies
        for in_cons in self._options['_connect'].values():
            in_cons = in_cons if isinstance(in_cons, list) else [in_cons]
            for parent_cons in in_cons:
                parent = parent_cons.split("/")[0]
                if parent not in self._options['_depends'] \
                        and parent != 'empty':
                    # We cannot use sets here since the order of
                    # dependecies matters in rare cases, e.g., collect_scs.
                    self._options['_depends'].append(parent)

    def get_options(self):
        '''
        Returns a dictionary of all given options
        '''
        return self._options

    def get_option(self, key):
        """
        Query an option.
        """
        if key not in self._defined_options:
            raise UAPError(
                "Cannot query undefined option %s in step %s." %
                (key, self.__module__))
        return self._options[key]

    def is_option_set_in_config(self, key):
        """
        Determine whether an optional option (that is, a non-required option)
        has been set in the configuration.
        """
        if key not in self._defined_options:
            raise UAPError(
                "Cannot query undefined option %s in step %s." %
                (key, self.get_step_name()))
        is_set = key in self._options
        if is_set:
            if isinstance(self._options[key], list):
                is_set = any([v is not None for v in self._options[key]])
            else:
                is_set = self._options[key] is not None
        return is_set

    def is_volatile(self):
        return self._options['_volatile']

    def add_dependency(self, parent):
        '''
        Add a parent step to this steps dependencies.

        parent -- parent step this step depends on
        '''
        if not isinstance(parent, AbstractStep):
            raise UAPError(
                "Error: parent argument must be an AbstractStep.")
        if parent == self:
            raise UAPError("Cannot add a node as its own dependency.")
        self.dependencies.append(parent)
        parent.children_step_names.add(str(self))

    def get_dependencies(self):
        return self.dependencies

    def get_input_runs(self):
        '''
        Return a dict which contains all runs per parent steps.
        '''
        input_runs = dict()
        for parent in self.get_dependencies():
            input_runs[parent.get_step_name()] = parent.get_runs()
        return input_runs

    def declare_runs(self):
        # fetch all incoming run IDs which produce reads...
        self.runs(self.get_run_ids_in_connections_input_files())
        self.check_required_out_connections()

    def check_required_out_connections(self):
        '''
        This functions tests if all required out connections
        were set by all runs.
        '''
        required_out = self.get_out_connections(with_optional=False)
        bad_runs = 0
        for run_id, run in self._runs.items():
            used_conns = set()
            for connection, content in run._output_files.items():
                used = any([fl is not None for fl in content.keys()])
                if used:
                    used_conns.add(connection)
            missings = required_out - used_conns
            if missings:
                bad_runs += 1
                logger.warning(
                    'Run "%s" of step "%s" misses the required '
                    'connections %s. To remove this warning pass '
                    'optional=True to the add_connection method in the '
                    'step constructor __init__ of "%s".' %
                    (run_id, self.get_step_name(), list(missings), self.get_step_type()))
            if bad_runs == 5:
                logger.warning('... Emitting connection test for further '
                               'runs of "%s".' % self.get_step_name())
                break
        if bad_runs:
            logger.warning(
                '[Deprecation] Unmet required connections '
                'may trigger an error in future version of the UAP.')

    def get_output_directory(self):
        '''
        Returns the step output directory.
        '''
        return os.path.join(
            self.get_pipeline().config['destination_path'],
            self.get_step_name()
        )

    def get_submit_script_file(self):
        if self._submit_script is None:
            self._submit_script = os.path.join(
                self.get_output_directory(),
                ".submit-%s.sh" % self.get_step_name()
            )
        return self._submit_script

    def runs(self, run_ids_connections_files):
        '''
        Abstract method this must be implemented by actual step.

        Raise NotImplementedError if subclass does not override this
        method.
        '''
        raise NotImplementedError()

    def execute(self, run_id, run):
        # get run_info objects
        with self.get_run(run_id) as run:
            logger.info("Run ID: %s" % run_id)
            # for each exec_group in that run ...
            for exec_group in run.get_exec_groups():
                # ... create a process pool
                with process_pool.ProcessPool(run) as pool:
                    # Clean up (use last ProcessPool for that)
                    if exec_group == run.get_exec_groups()[-1]:
                        logger.info("Telling pipeline to clean up!")
                        pool.clean_up_temp_paths()

                    for poc in exec_group.get_pipes_and_commands():
                        # for each pipe or command (poc)
                        # check if it is a pipeline ...
                        if isinstance(poc, pipeline_info.PipelineInfo):
                            # ... create a pipeline ...
                            with pool.Pipeline(pool) as pipeline:
                                for command in poc.get_commands():
                                    pipeline.append(
                                        command.get_command(),
                                        stdout_path=command.get_stdout_path(),
                                        stderr_path=command.get_stderr_path())
                        elif isinstance(poc, command_info.CommandInfo):
                            pool.launch(
                                poc.get_command(),
                                stdout_path=poc.get_stdout_path(),
                                stderr_path=poc.get_stderr_path())

    def get_runs(self):
        '''
        Getter method for runs of this step.

        If there are no runs as this method is called, they are created here.
        '''
        # create runs if they don't exist yet
        if not self._runs:
            # if _BREAK: true is specified in the configuration,
            # return no runs and thus cut off further processing
            if '_BREAK' in self._options and self._options['_BREAK']:
                return dict()

            self._runs = dict()
            self.declare_runs()

            # define file dependencies
            for run_id in self._runs.keys():
                pipeline = self.get_pipeline()
                run = self.get_run(run_id)
                for connection in run.get_output_files_abspath().keys():
                    for output_path, input_paths in \
                            run.get_output_files_abspath()[connection].items():
                        # proceed if we have normal output_path/input_paths
                        if output_path is not None and input_paths is not None:
                            # store file dependencies
                            pipeline.add_file_dependencies(
                                output_path, input_paths)
                            # create task ID
                            task_id = '%s/%s' % (str(self), run_id)
                            pipeline.add_task_for_output_file(
                                output_path, task_id)
                            # No input paths? Add empty string NOT None
                            # as file name
                            if len(input_paths) == 0:
                                pipeline.add_task_for_input_file(
                                    "", task_id)
                            for input_path in input_paths:
                                pipeline.add_task_for_input_file(
                                    input_path, task_id)

        # now that _runs exists, it remains constant, just return it
        return self._runs

    def reset_run_caches(self):
        for run in self.get_runs().values():
            run.fsc.clear()

    def get_run_ids(self):
        '''
        Returns sorted list of runs generated by step.
        '''
        return sorted(self.get_runs().keys())

    def get_step_name(self):
        '''
        Returns this steps name.

        Returns the step name which is initially equal to the step type
        (== module name)  but can be changed via set_step_name() or via
        the YAML configuration.
        '''
        return self._step_name

    def get_step_type(self):
        '''
        Returns the original step name (== module name).
        '''
        return self.__module__

    def remove_ping_file(self, ping_path, bad_copy=False):
        # don't remove the ping file, rename it so we can inspect it later
        try:
            backup = self.get_pipeline().args.debugging
        except AttributeError:
            backup = False
        suffix = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
        if os.path.exists(ping_path):
            try:
                out_w_suffix = ping_path + '.' + suffix
                if bad_copy:
                    out_w_bad = ping_path + '.bad'
                    os.rename(ping_path, out_w_bad)
                    if backup:
                        copyfile(out_w_bad, out_w_suffix)
                    logger.debug('The run ping file "%s" was moved to "%s" '
                                 'and copied to "%s" by host %s.' %
                                 (ping_path, out_w_bad, out_w_suffix,
                                  socket.gethostname()))
                elif backup:
                    os.rename(ping_path, out_w_suffix)
                    logger.debug('The run ping file "%s" was moved to "%s" '
                                 'by host %s.' %
                                 (ping_path, out_w_suffix,
                                  socket.gethostname()))
                else:
                    os.unlink(ping_path)
                    logger.debug('The run ping file "%s" was removed by %s.' %
                                 (ping_path, socket.gethostname()))
            except OSError as e:
                logger.debug('The run ping file "%s" could not be moved: %s' %
                             (ping_path, str(e)))
                pass
        else:
            logger.debug('This run ping file was not found: %s' %
                         ping_path)

    def run(self, run_id):
        '''
        Create a temporary output directory and execute a run. After the run
        has finished, it is checked that all output files are in place and
        the output files are moved to the final output location. Finally,
        YAML annotations are written.
        '''

        # this is the run we'll execute now
        run = self.get_run(run_id)

        # create the output directory if it doesn't exist yet
        if not os.path.isdir(run.get_output_directory()):
            os.makedirs(run.get_output_directory())

        # now write the run ping file
        executing_ping_path = run.get_executing_ping_file()

        if os.path.exists(executing_ping_path):
            raise UAPError("%s/%s seems to be already running, exiting..."
                           % (self, run_id))
        queued_ping_path = run.get_queued_ping_file()
        try:
            with open(queued_ping_path, 'r') as buff:
                info = yaml.load(buff, Loader=yaml.FullLoader)
            job_id = info['cluster job id']
        except (IOError, KeyError):
            job_id = None

        # create a temporary directory for the output files
        temp_directory = run.get_temp_output_directory()
        os.makedirs(temp_directory)

        # prepare known_paths
        known_paths = dict()
        for tag, tag_info in run.get_output_files_abspath().items():
            for output_path, input_paths in tag_info.items():
                # add the real output path
                if output_path is not None and input_paths is not None:
                    known_paths[output_path] = {
                        'designation': 'output',
                        'label': os.path.basename(output_path),
                        'type': 'step_file'}
                    # ...and also add the temporary output path
                    known_paths[
                        os.path.join(temp_directory, os.path.basename(
                            output_path))] = {
                        'designation': 'output',
                        'label': "%s\\n(%s)" %
                                (os.path.basename(output_path), tag),
                        'type': 'step_file',
                        'real_path': output_path}
                    for input_path in input_paths:
                        if input_path is not None:
                            known_paths[input_path] = {
                                'designation': 'input',
                                'label': os.path.basename(input_path),
                                'type': 'step_file'}

        # now write the run ping file
        executing_ping_info = dict()
        executing_ping_info['start_time'] = datetime.now()
        executing_ping_info['host'] = socket.gethostname()
        executing_ping_info['pid'] = os.getpid()
        executing_ping_info['user'] = pwd.getpwuid(os.getuid())[0]
        executing_ping_info['temp_directory'] = run.get_temp_output_directory()
        if job_id:
            executing_ping_info['cluster job id'] = job_id

        with open(executing_ping_path, 'w') as f:
            f.write(yaml.dump(executing_ping_info, default_flow_style=False))

        executing_ping_pid = os.fork()
        if executing_ping_pid == 0:
            # this is the chid process
            try:
                signal.signal(signal.SIGTERM, signal.SIG_DFL)
                signal.signal(signal.SIGINT, signal.SIG_IGN)
                while True:
                    time.sleep(AbstractStep.PING_RENEW)
                    # if the executing ping file is gone and the touching
                    # operation fails, then SO BE IT!
                    os.utime(executing_ping_path, None)
            finally:
                os._exit(0)

        def kill_exec_ping():
            try:
                os.kill(executing_ping_pid, signal.SIGTERM)
                os.waitpid(executing_ping_pid, 0)
            except OSError:
                # if the ping process was already killed, it's gone anyway
                pass
            self.remove_ping_file(executing_ping_path)

        p = self.get_pipeline()
        def ping_on_term(signum, frame):
            logger.warning('Recived SIGTERM and moving execution ping file...')
            kill_exec_ping()
            self.remove_ping_file(queued_ping_path, bad_copy=True)
            p.caught_signal = signum
            process_pool.ProcessPool.kill()
            raise UAPError('Recived TERM signal (canceled job).')
        def ping_on_int(signum, frame):
            logger.warning('Recived SIGINT and moving execution ping file...')
            kill_exec_ping()
            self.remove_ping_file(queued_ping_path, bad_copy=True)
            p.caught_signal = signum
            process_pool.ProcessPool.kill()
            raise UAPError('Recived INT signal (keybord interrupt).')
        original_term_handler = signal.signal(signal.SIGTERM, ping_on_term)
        original_int_handler = signal.signal(signal.SIGINT, ping_on_int)

        self.start_time = datetime.now()
        message = "[START] starting %s/%s on %s" % \
            (self, run_id, socket.gethostname())
        if job_id:
            message += " with job id %s" % job_id
        p.notify(message)
        caught_exception = None
        self._state = AbstractStep.states.EXECUTING
        base_working_dir = os.getcwd()
        os.chdir(run.get_temp_output_directory())
        try:
            self.execute(run_id, run)
        except BaseException:
            # Oh my. We have a situation. This is awkward. Tell the process
            # pool to wrap up. This way, we can try to get process stats before
            # shutting everything down.
            process_pool.ProcessPool.kill()
            # Store the exception, re-raise it later
            caught_exception = sys.exc_info()
            error = ''.join(traceback.format_exception(
                    *caught_exception)[-2:]).strip()
            logger.debug(error)
        finally:
            signal.signal(signal.SIGTERM, original_term_handler)
            signal.signal(signal.SIGINT, original_int_handler)
            self._state = AbstractStep.states.DEFAULT  # changes relative paths
            os.chdir(base_working_dir)

        self.end_time = datetime.now()
        # step has completed invalidate the FS cache because things have
        # changed by now...
        run.reset_fsc()

        to_be_moved = dict()
        if not p.caught_signal and not caught_exception:
            # if we're here, we can assume the step has finished successfully
            # now log file stats

            try:
                for tag in run.get_output_files().keys():
                    for out_file in run.get_output_files()[tag].keys():
                        # don't try to rename files if they were not meant to exist
                        # in our temporary directory
                        # 1. out_file should not be None (empty output connection)
                        # 2. out_file should not contain a '/' (file belongs to a
                        #    source step)
                        if out_file is None or '/' in out_file:
                            continue
                        source_path = os.path.join(
                            run.get_temp_output_directory(),
                            os.path.basename(out_file)
                        )
                        new_path = os.path.join(
                            run.get_output_directory(),
                            os.path.basename(out_file))
                        # first, delete a possibly existing volatile placeholder
                        # file
                        path_volatile = new_path + AbstractStep.VOLATILE_SUFFIX
                        if os.path.exists(path_volatile):
                            logger.info("Now deleting: %s" % path_volatile)
                            os.unlink(path_volatile)
                        if os.path.exists(source_path):
                            known_paths.pop(source_path, None)
                            known_paths.setdefault(new_path, dict())
                            if known_paths[new_path]['designation'] == 'output':
                                to_be_moved[source_path] = new_path
                                size = run.fsc.getsize(source_path)
                                mtime = datetime.fromtimestamp(
                                    run.fsc.getmtime(source_path))
                                known_paths[new_path]['size'] = size
                                known_paths[new_path]['modification time'] = mtime
                            if known_paths[new_path]['type'] != 'step_file':
                                logger.debug(
                                    "Set %s 'type' info to 'step_file'" % new_path)
                                known_paths[new_path]['type'] = 'step_file'
                        else:
                            raise UAPError('The step failed to produce an '
                                           'announced output file: "%s".\n'
                                           'Source file doesn\'t exists: "%s"'
                                           % (out_file, source_path))
            except BaseException:
                caught_exception = sys.exc_info()

        pool = None

        class SignalError(Exception):
            def __init__(self, signum):
                self.signum = signum
                m = "Recived signal %s during hashing!" % \
                    process_pool.ProcessPool.SIGNAL_NAMES[signum]
                super(SignalError, self).__init__(m)
        if caught_exception is None and to_be_moved:
            p.notify("[INFO] %s/%s hashing %d output file(s)." %
                     (str(self), run_id, len(to_be_moved)))
            if p.has_interactive_shell() \
                    and logger.getEffectiveLevel() > 20:
                show_progress = True
            else:
                show_progress = False
            try:
                def stop(signum, frame):
                    raise SignalError(signum)
                original_term_handler = signal.signal(signal.SIGTERM, stop)
                original_int_handler = signal.signal(signal.SIGINT, stop)
                pool = multiprocessing.Pool(self.get_cores())
                total = len(to_be_moved)
                file_iter = pool.imap(misc.sha_and_file, to_be_moved.keys())
                file_iter = tqdm(
                    file_iter,
                    total=total,
                    leave=False,
                    bar_format='{desc}:{percentage:3.0f}%|{bar:10}{r_bar}',
                    disable=not show_progress,
                    desc='files')
                for i, (hashsum, path) in enumerate(file_iter):
                    run.fsc.sha256sum_of(to_be_moved[path], value=hashsum)
                    known_paths[to_be_moved[path]]['sha256'] = hashsum
                    if not show_progress:
                        logger.info("sha256 [%d/%d] %s %s" %
                                    (i + 1, total, hashsum, path))
            except BaseException:
                caught_exception = sys.exc_info()
                try:
                    # removing the progress bar
                    file_iter.close()
                except BaseException:
                    pass
                error = caught_exception[1]
                if caught_exception[0] is SignalError:
                    p.caught_signal = error.signum
                logger.error(error)
                if pool:
                    pool.terminate()
            else:
                pool.close()
            signal.signal(signal.SIGTERM, original_term_handler)
            signal.signal(signal.SIGINT, original_int_handler)

        run.add_known_paths(known_paths)
        if not p.caught_signal and not caught_exception:
            try:
                for source_path, new_path in to_be_moved.items():
                    logger.debug("Moving %s to %s." % (source_path, new_path))
                    os.rename(source_path, new_path)
            except BaseException:
                caught_exception = sys.exc_info()

        error = None
        if p.caught_signal is not None:
            signum = p.caught_signal
            signame = process_pool.ProcessPool.SIGNAL_NAMES[signum]
            error = 'Pipeline stopped because it caught signal %d - %s' % \
                    (signum, signame)
        elif caught_exception is not None:
            error = ''.join(traceback.format_exception(
                    *caught_exception)[-2:]).strip()
        annotation_path = run.write_annotation_file(
            run.get_output_directory(), error=error, job_id=job_id)

        kill_exec_ping()
        self._state = AbstractStep.states.DEFAULT

        if error:
            message = "[BAD] %s/%s failed on %s after %s\n" % \
                      (str(self), run_id, socket.gethostname(),
                       misc.duration_to_str(self.end_time - self.start_time))
            message += "Here are the details: " + annotation_path + '\n'
            attachment = None
            if os.path.exists(annotation_path + '.png'):
                attachment = dict()
                attachment['name'] = 'details.png'
                attachment['data'] = open(annotation_path + '.png').read()
            p.notify(message, attachment)
            self.remove_ping_file(queued_ping_path, bad_copy=True)
            if caught_exception is not None:
                raise caught_exception[1].with_traceback(caught_exception[2])

        else:
            # finally, remove the temporary directory if it's empty
            try:
                os.rmdir(temp_directory)
            except OSError as e:
                logger.info('Coult not remove temp dir "%s": %s' %
                            (temp_directory, e))
            temp = os.path.normpath(os.path.join(temp_directory, '..'))
            try:
                os.rmdir(temp)
            except OSError:
                # there may still be tasks in process
                pass

            remaining_task_info = self.get_run_info_str()

            message = "[OK] %s/%s successfully finished on %s after %s\n" % \
                      (str(self), run_id, socket.gethostname(),
                       misc.duration_to_str(self.end_time - self.start_time))
            message += str(self) + ': ' + remaining_task_info + "\n"
            attachment = None
            if os.path.exists(annotation_path + '.png'):
                attachment = dict()
                attachment['name'] = 'details.png'
                attachment['data'] = open(annotation_path + '.png').read()
            p.notify(message, attachment)
            self.remove_ping_file(queued_ping_path)

            self._reset()

        if pool is not None:
            pool.join()

    def get_pre_commands(self):
        """
        Return dictionary with commands to execute before starting any other
        command of this step
        """
        return self._pre_command

    def get_module_loads(self):
        """
        Return dictionary with module load commands to execute before starting
        any other command of this step
        """
        return self._module_load

    def get_tool(self, key):
        """
        Return full path to a configured tool.
        """
        if key not in self._tools:
            raise UAPError("Tool %s unknown. Maybe you forgot to use "
                           "self.require_tool('%s')" % (key, key))
        return self._tools[key]

    def get_path_tool(self):
        '''
        Returns a dict with a tool name for each tool paths.
        '''
        return {' '.join(path): tool for tool, path in self._tools.items()}

    @property
    def used_tools(self):
        return set(self._tools.keys())

    def get_module_unloads(self):
        """
        Return dictionary with module unload commands to execute before
        starting any other command of this step
        """
        return self._module_unload

    def get_post_commands(self):
        """
        Return dictionary with commands to execute after finishing any other
        command of this step
        """
        return self._post_command

    def get_run_info_str(self, progress=False, do_hash=False):
        count = {}
        runs = self.get_runs()
        run_iter = tqdm(runs, total=len(runs), desc='runs',
                        bar_format='{desc}:{percentage:3.0f}%|{bar:10}{r_bar}',
                        disable=not progress, leave=False)
        try:
            for run in run_iter:
                if isinstance(run, str):
                    run = self.get_run(run)
                state = run.get_state(do_hash=do_hash)
                if state not in count:
                    count[state] = 0
                count[state] += 1
        except BaseException:
            run_iter.close()
            raise
        return ', '.join(["%d %s" % (count[_], _.lower())
                          for _ in self.get_pipeline().states.order if _ in count])

    def append_pipeline_log(self, log):
        if len(self._pipeline_log) == 0:
            self._pipeline_log = log
        else:
            for k in log.keys():
                if k == 'process_watcher':
                    for k2 in log[k].keys():
                        if k2 == 'max':
                            for _ in log[k][k2].keys():
                                if _ == 'sum':
                                    for k3 in self._pipeline_log[k][k2][_].keys():
                                        self._pipeline_log[k][k2][_][k3] = \
                                            max(self._pipeline_log[k][k2][_][k3],
                                                log[k][k2][_][k3])
                                else:
                                    self._pipeline_log[k][k2][_] = log[k][k2][_]
                        else:
                            self._pipeline_log[k][k2].update(log[k][k2])

                else:
                    if log[k].__class__ == list:
                        self._pipeline_log[k].extend(log[k])
                    else:
                        self._pipeline_log[k].update(log[k])

    def __str__(self):
        return self._step_name

    @classmethod
    def get_step_class_for_key(cls, key):
        """
        Returns a step (or source step) class for a given key which corresponds
        to the name of the module the class is defined in. Pass 'cutadapt' and
        you will get the cutadapt.Cutadapt class which you may then instantiate.
        """

        check_classes = [AbstractSourceStep, AbstractStep]
        for index, c in enumerate(check_classes):

            classes = [_ for _ in inspect.getmembers(__import__(key),
                                                     inspect.isclass)
                       if c in _[1].__bases__]

            for k in range(index):
                classes = [_ for _ in classes if _[1] != check_classes[k]]
            if len(classes) > 0:
                if len(classes) != 1:
                    raise UAPError("need exactly one subclass of %s in %s"
                                   % (c, key))
                return classes[0][1]

        raise UAPError("No suitable class found for module %s." % key)

    def set_cores(self, cores):
        """
        Specify the number of CPU cores this step will use.
        """
        if not isinstance(cores, int) or cores < 1:
            raise UAPError('[%s] Cores need to be a positive integer, not %s.'
                           % (self.get_step_name(), cores))
        self._cores = cores

    def get_cores(self):
        """
        Returns the number of cores used in this step.
        """
        return self._cores

    def add_input_connection(self, connection):
        '''
        Add an input connection to this step
        '''
        self.add_connection('in/%s' % connection)

    def add_output_connection(self, connection):
        '''
        Add an output connection to this step
        '''
        self.add_connection('out/%s' % connection)

    def add_connection(self, connection,
                       optional=False, format=None, description=None):
        """
        Add a connection, which must start with 'in/' or 'out/'.
        :type format: (str) Data format passed in the connection.
        :type description: (str) Explain the connection.
        """
        if not (connection[0:3] == 'in/' or connection[0:4] == 'out/'):
            raise UAPError("A connection must start with 'in/' or 'out/'.")
        if connection[0:3] == 'in/':
            self.needs_parents = True
        if optional is True:
            self._optional_connections.add(connection)
        else:
            self._connections.add(connection)
        if format is not None:
            self._connection_formats[connection] = format
        if description is not None:
            self._connection_descriptions[connection] = \
                re.sub(r'\s+', ' ', description)

    def get_connections(self, with_optional=True):
        """
        Return all connections for this step
        """
        connections = self._connections.copy()
        if with_optional is True:
            connections = connections.union(self._optional_connections)
        return connections

    def get_in_connections(self, with_optional=True, strip_prefix=False):
        """
        Return all in-connections for this step
        """
        connections = self._connections.copy()
        if with_optional is True:
            connections = connections.union(self._optional_connections)
        in_connections = set()
        for connection in connections:
            if connection[0:3] == "in/":
                if strip_prefix is True:
                    con = connection[3:]
                else:
                    con = connection
                in_connections.add(con)
        return in_connections

    def get_out_connections(self, with_optional=True, strip_prefix=False):
        """
        Return all out-connections for this step
        """
        connections = self._connections.copy()
        if with_optional is True:
            connections = connections.union(self._optional_connections)
        out_connections = set()
        for connection in connections:
            if connection[0:4] == "out/":
                if strip_prefix is True:
                    con = connection[4:]
                else:
                    con = connection
                out_connections.add(con)
        return out_connections

    def require_tool(self, tool):
        """
        Declare that this step requires an external tool. Query it later with
        *get_tool()*.
        """
        if self.get_pipeline() is not None:
            if tool not in self.get_pipeline().config['tools']:
                raise UAPError(
                    "%s requires the tool %s but it's not declared in "
                    "the configuration." %
                    (self, tool))
            self._tools[tool] = self.get_pipeline(
            ).config['tools'][tool]['path']
            if 'pre_command' in self.get_pipeline().config['tools'][tool]:
                self._pre_command[tool] = self.get_pipeline(
                ).config['tools'][tool]['pre_command']
            if 'module_load' in self.get_pipeline().config['tools'][tool]:
                self._module_load[tool] = self.get_pipeline(
                ).config['tools'][tool]['module_load']
            if 'module_unload' in self.get_pipeline().config['tools'][tool]:
                self._module_unload[tool] = self.get_pipeline(
                ).config['tools'][tool]['module_unload']
            if 'post_command' in self.get_pipeline().config['tools'][tool]:
                self._post_command[tool] = self.get_pipeline(
                ).config['tools'][tool]['post_command']
        else:
            self._tools[tool] = True

    def add_option(self, key, *option_types, **kwargs):
        """
        Add an option. Multiple types may be specified.
        """
        if 'optional' not in kwargs:
            kwargs['optional'] = False
        for _ in ['default', 'description', 'choices']:
            if _ not in kwargs:
                kwargs[_] = None

        if key[0] == '_':
            raise UAPError(
                "Option key must not start with an underscore: %s." % key)
        if key in self._defined_options:
            raise UAPError("Option %s is already defined." % key)
        if len(option_types) == 0:
            raise UAPError("No option type specified for option %s." % key)
        if len(option_types) > 1 and kwargs['choices'] is not None:
            raise UAPError(
                "You cannot define choices if multiple options types "
                "are defined (%s)." %
                key)
        for option_type in option_types:
            if option_type not in [int, float, str, bool, list, dict]:
                raise UAPError("Invalid type for option %s: %s."
                               % (key, option_type))
        if kwargs['optional'] and (kwargs['default'] is not None):
            if type(kwargs['default']) not in option_types:
                raise UAPError(
                    "In step: (%s) option: (%s) Type of default value (%s) "
                    "does not match any of the declared possible types (%s)." %
                    (self, key, type(kwargs['default']), option_types))

        info = dict()
        info['types'] = misc.type_tuple(option_types)
        for _ in ['optional', 'default', 'description', 'choices']:
            info[_] = kwargs[_]

        if info['description'] is not None:
            if not isinstance(info['description'], str):
                raise UAPError(
                    'The description of option %s in step %s is not a string.' %
                    (key, self))
            # collapse whites spaces
            info['description'] = re.sub(r'\s+', ' ', info['description'])

        self._defined_options[key] = info

    def find_upstream_info_for_input_paths_as_set(self, input_paths,
                                                  key, expected=1):
        task_ids = set()
        for path in input_paths:
            task_ids.add(self.get_pipeline().task_id_for_output_file[path])
        results = set()
        for task_id in task_ids:
            task = self.get_pipeline().task_for_task_id[task_id]
            step = task.step
            run_id = task.run_id
            run = step._runs[run_id]
            if run.has_public_info(key):
                results.add(run.get_public_info(key))
            results |= self.find_upstream_info_for_input_paths_as_set(
                task.input_files(), key, None)

        if expected is not None:
            if len(results) != expected:
                raise UAPError(
                    "Unable to determine upstream %s info from %s." %
                    (key, self))
        return results

    def find_upstream_info_for_input_paths(self, input_paths, key):
        """
        Find a piece of public information in all upstream steps. If the
        information is not found or defined in more than one upstream step,
        this will crash.
        """

        result = self.find_upstream_info_for_input_paths_as_set(
            input_paths, key, expected=1)
        return list(result)[0]

    def get_run_ids_in_connections_input_files(self):
        '''
        Return a dictionary with all run IDs from parent steps, the
        in connections they provide data for, and the names of the files::

           run_id_1:
               in_connection_1: [input_path_1, input_path_2, ...]
               in_connection_2: ...
           run_id_2: ...

        Format of ``in_connection``: ``in/<connection>``. Input paths are
        absolute.
        '''

        cc = ConnectionsCollector(self.get_step_name())
        self._options.setdefault('_connect', dict())

        # Check if set in-connections are defined in the step class
        # and collect out connections for later check.
        set_out_connections = set()
        used_out_connections = set()
        for in_conn, out_conn in self._options['_connect'].items():
            if in_conn not in self.get_in_connections():
                raise UAPError('_connect: unknown input connection "%s" '
                               'found. Available connections are %s' %
                               (in_conn, list(self.get_in_connections())))
            out_conn = out_conn if isinstance(out_conn, list) else [out_conn]
            set_out_connections = set_out_connections.union(set(out_conn))

        if 'empty' in set_out_connections:
            logger.warning(
                '[%s] "empty" in _connect is deprecated and will be '
                'ignored.' %
                self.get_step_name())
            set_out_connections.discard('empty')

        # For each parent step ...
        for parent in self.get_dependencies():
            if not parent.get_runs():
                raise UAPError('The step "%s" produces no output.' %
                               parent.get_step_name())
            logger.debug('Connecting "%s" to "%s".' %
                         (parent.get_step_name(), self.get_step_name()))
            # ... look for connection to add
            used_conns = cc.connect(parent, self, self._options['_connect'])
            if not used_conns:
                # ... or add connections with the same name.
                logger.debug('Parent "%s" not connected to child "%s". '
                             'Hence connecting equally named connections.' %
                             (parent.get_step_name(), self.get_step_name()))
                used_conns = cc.connect(parent, self)
            if not used_conns:
                raise UAPError('No connections could be made between '
                               '"%s" and its dependency "%s".' %
                               (self.get_step_name(), parent.get_step_name()))
            used_out_connections = used_out_connections.union(used_conns)

        # Check if all required connections are sattisfied.
        required_connections = self.get_in_connections(with_optional=False)
        missing = required_connections - cc.existing_connections
        if missing:
            logger.warning(
                '_connect: The required connection %s of step '
                '"%s" is not satisfied. To remove this warning pass '
                'optional=True to the add_connection method in the step '
                'constructor __init__ of "%s".' %
                (missing, self.get_step_type(), self.get_step_type()))
            logger.warning(
                '[Deprecation] Unmet required connections may trigger '
                'an error in future version of the UAP.')

        # Check if all set out connections were recognized.
        unrecognized = set_out_connections - used_out_connections
        if len(unrecognized) > 0:
            raise UAPError('For the following connections into step "%s" '
                           'no parent run could be found: %s.' %
                           (self.get_step_name(), list(unrecognized)))

        return cc


class AbstractSourceStep(AbstractStep):
    """
    A subclass all source steps inherit from and which distinguishes source
    steps from all real processing steps because they do not yield any tasks,
    because their "output files" are in fact files which are already there.

    Note that the name might be a bit misleading because this class only
    applies to source steps which 'serve' existing files. A step which has
    no input but produces input data for other steps and actually has to do
    something for it, on the other hand, would be a normal AbstractStep
    subclass because it produces tasks.
    """

    def __init__(self, pipeline):
        super(AbstractSourceStep, self).__init__(pipeline)

