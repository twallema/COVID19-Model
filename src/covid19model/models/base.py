import inspect
import itertools

import numpy as np
from scipy.integrate import solve_ivp
import xarray
import pandas as pd
from collections import OrderedDict
import copy

class BaseModel:
    """
    Initialise the models

    Parameters
    ----------
    To initialise the model, provide following inputs:

    states : dictionary
        contains the initial values of all non-zero model states
        e.g. {'S': N, 'E': np.ones(n_stratification)} with N being the total population and n_stratifications the number of stratified layers
        initialising zeros is thus not required
    parameters : dictionary
        containing the values of all parameters (both stratified and not)
        these can be obtained with the function parameters.get_COVID19_SEIRD_parameters()
    time_dependent_parameters : dictionary, optional
        Optionally specify a function for time-dependent parameters. The
        signature of the function should be ``fun(t, param, ...)`` taking
        the time, the initial parameter value, and potentially additional
        keyword argument, and should return the new parameter value for
        time `t`.
    """

    state_names = None
    parameter_names = None
    parameters_stratified_names = None
    stratification = None
    coordinates = None

    def __init__(self, states, parameters, time_dependent_parameters=None,
                 discrete=False):
        self.parameters = parameters
        self.initial_states = states
        self.time_dependent_parameters = time_dependent_parameters
        self.discrete = discrete

        if self.stratification:
            self.stratification_size = []
            for axis in self.stratification:
                if not axis in parameters:
                    raise ValueError(
                        "stratification parameter '{0}' is missing from the specified "
                        "parameters dictionary".format(axis)
                    )
                self.stratification_size.append(parameters[axis].shape[0])
        else:
            self.stratification_size = [1]

        if time_dependent_parameters:
            self._validate_time_dependent_parameters()
        else:
            self._function_parameters = []

        self._validate()

    def _fill_initial_state_with_zero(self):
        for state in self.state_names:
            if state in self.initial_states:
                state_values = self.initial_states[state]

    def _validate_parameter_function(self, func):
        # Validate the function passed to time_dependent_parameters
        sig = inspect.signature(func)
        keywords = list(sig.parameters.keys())
        if keywords[0] != "t":
            raise ValueError(
                "The first parameter of the parameter function should be 't'"
            )
        if keywords[1] == "param":
            return keywords[2:],True
        else:
            return keywords[1:],False

    def _validate_time_dependent_parameters(self):
        # Validate arguments of compliance definition

        extra_params = []
        self._relative_time_dependent_value = []

        #all_param_names = self.parameter_names + self.parameters_stratified_names

        all_param_names = self.parameter_names.copy()

        for lst in self.parameters_stratified_names:
            all_param_names.extend(lst)

        if self.stratification:
            all_param_names.extend(self.stratification)

        for param, func in self.time_dependent_parameters.items():
            if param not in all_param_names:
                raise ValueError(
                    "The specified time-dependent parameter '{0}' is not an "
                    "existing model parameter".format(param))
            kwds,relative = self._validate_parameter_function(func)
            extra_params.append(kwds)
            self._relative_time_dependent_value.append(relative)

        self._function_parameters = extra_params

    def _validate(self):
        """
        This does some basic validation of the model + initialization:

        1) Validation of the integrate function to ensure it matches with
        the specified `state_names`, `parameter_names`, etc.
        This is actually a validation of the model class itself, but it is
        easier to do this only on initialization of a model instance.

        2) Validation of the actual initialization with initial values for the
        states and parameter values.
        TODO: For now, we require that those are passed in the exact same
        order, but this requirement could in principle be relaxed, if we ensure
        to pass the states and parameters as keyword arguments and not as
        positional arguments to the `integrate` function.

        """
        # Validate Model class definition (the integrate function)
        sig = inspect.signature(self.integrate)
        keywords = list(sig.parameters.keys())
        if keywords[0] != "t":
            raise ValueError(
                "The first parameter of the 'integrate' function should be 't'"
            )
        N_states = len(self.state_names)
        integrate_states = keywords[1 : 1 + N_states]
        if integrate_states != self.state_names:
            raise ValueError(
                "The states in the 'integrate' function definition do not match "
                "the state_names: {0} vs {1}".format(integrate_states, self.state_names)
            )

        integrate_params = keywords[1 + N_states :]
        specified_params = self.parameter_names.copy()

        if self.parameters_stratified_names:
            for stratified_names in self.parameters_stratified_names:
                if stratified_names:
                    specified_params += stratified_names
        if self.stratification:
            specified_params += self.stratification

        if integrate_params != specified_params:
            raise ValueError(
                "The parameters in the 'integrate' function definition do not match "
                "the parameter_names + parameters_stratified_names + stratification: "
                "{0} vs {1}".format(integrate_params, specified_params)
            )

        # additional parameters from time-dependent parameter functions
        # are added to specified_params after the above check

        if self._function_parameters:
            extra_params = [item for sublist in self._function_parameters for item in sublist]
            # TODO check that it doesn't duplicate any existing parameter
            # Line below removes duplicate arguments
            extra_params = OrderedDict((x, True) for x in extra_params).keys()
            specified_params += extra_params
            self._n_function_params = len(extra_params)
        else:
            self._n_function_params = 0

        # Validate the params
        if set(self.parameters.keys()) != set(specified_params):
            raise ValueError(
                "The specified parameters don't exactly match the predefined parameters. "
                "Redundant parameters: {0}. Missing parameters: {1}".format(
                set(self.parameters.keys()).difference(set(specified_params)),
                set(specified_params).difference(set(self.parameters.keys())))
            )

        self.parameters = {param: self.parameters[param] for param in specified_params}

        # Validate the initial_states / stratified params having the correct length

        def validate_stratified_parameters(values, name, object_name,i):
            values = np.asarray(values)
            if values.ndim != 1:
                raise ValueError(
                    "A {obj} value should be a 1D array, but {obj} '{name}' has "
                    "dimension {val}".format(
                        obj=object_name, name=name, val=values.ndim
                    )
                )
            if len(values) != self.stratification_size[i]:
                raise ValueError(
                    "The stratification parameter '{strat}' indicates a "
                    "stratification size of {strat_size}, but {obj} '{name}' "
                    "has length {val}".format(
                        strat=self.stratification[i], strat_size=self.stratification_size[i],
                        obj=object_name, name=name, val=len(values)
                    )
                )

        def validate_initial_states(values, name, object_name):
            values = np.asarray(values)
            if list(values.shape) != self.stratification_size:
                raise ValueError(
                    "The stratification parameters '{strat}' indicates a "
                    "stratification size of {strat_size}, but {obj} '{name}' "
                    "has length {val}".format(
                        strat=self.stratification, strat_size=self.stratification_size,
                        obj=object_name, name=name, val=list(values.shape)
                    )
                )

        # the size of the stratified parameters
        if self.parameters_stratified_names:
            i = 0
            for stratified_names in self.parameters_stratified_names:
                if stratified_names:
                    for param in stratified_names:
                        validate_stratified_parameters(
                            self.parameters[param], param, "stratified parameter",i
                        )
                i = i + 1

        # the size of the initial states + fill in defaults
        for state in self.state_names:
            if state in self.initial_states:
                # if present, check that the length is correct
                validate_initial_states(
                    self.initial_states[state], state, "initial state"
                )

            else:
                # otherwise add default of 0
                self.initial_states[state] = np.zeros(self.stratification_size)

        # validate the states (using `set` to ignore order)
        if set(self.initial_states.keys()) != set(self.state_names):
            raise ValueError(
                "The specified initial states don't exactly match the predefined states"
            )
        # sort the initial states to match the state_names
        self.initial_states = {state: self.initial_states[state] for state in self.state_names}

    @staticmethod
    def integrate():
        """to overwrite in subclasses"""
        raise NotImplementedError

    def _create_fun(self, start_date, excess_time):
        """Convert integrate statement to scipy-compatible function"""

        def func(t, y, pars={}):
            """As used by scipy -> flattend in, flattend out"""

            # update time-dependent parameter values
            params = pars.copy()

            if self.time_dependent_parameters:
                if excess_time is not None:
                    date = self.int_to_date(start_date, t, excess_time)
                else:
                    date = t
                for i, (param, func) in enumerate(self.time_dependent_parameters.items()):
                    func_params = {key: params[key] for key in self._function_parameters[i]}
                    if self._relative_time_dependent_value[i] == True:
                        params[param] = func(date, pars[param], **func_params)
                    else:
                        params[param] = func(date, **func_params)
            
            if self._n_function_params > 0:
                model_pars = list(params.values())[:-self._n_function_params]
            else:
                model_pars = list(params.values())

            # for the moment assume sequence of parameters, vars,... is correct
            size_lst=[len(self.state_names)]
            for size in self.stratification_size:
                size_lst.append(size)
            y_reshaped = y.reshape(tuple(size_lst))

            dstates = self.integrate(t, *y_reshaped, *model_pars)
            return np.array(dstates).flatten()

        return func

    def _sim_single(self, time, start_date=None, excess_time=None):
        """"""
        fun = self._create_fun(start_date, excess_time)

        t0, t1 = time
        t_eval = np.arange(start=t0, stop=t1 + 1, step=1)

        if self.discrete == False:
            output = solve_ivp(fun, time,
                           list(itertools.chain(*self.initial_states.values())),
                           args=[self.parameters], t_eval=t_eval)
        else:
            output = self.solve_discrete(fun,time,list(itertools.chain(*self.initial_states.values())),
                            args=self.parameters)

        # map to variable names
        return self._output_to_xarray_dataset(output)

    def solve_discrete(self,fun,time,y,args):
        # Preparations
        y=np.asarray(y) # otherwise error in func : y.reshape does not work
        y=np.reshape(y,[y.size,1])
        y_prev=y
        # Iteration loop
        t_lst=[time[0]]
        t = time[0]
        while t < time[1]:
            out = fun(t,y_prev,args)
            y_prev=out
            out = np.reshape(out,[out.size,1])
            y = np.append(y,out,axis=1)
            t = t + 1
            t_lst.append(t)
        # Make a dictionary with output
        output = {
            'y':    y,
            't':    t_lst
        }
        return output

    def date_to_diff(self, start_date, date, excess_time):
        """
        Convert date string to int (i.e. number of days since day 0 of simulation,
        which is excess_time days before start_date)
        """
        return int((pd.to_datetime(date)-pd.to_datetime(start_date))/pd.to_timedelta('1D'))+excess_time

    def int_to_date(self, start_date, t, excess_time):
        date = pd.to_datetime(start_date) + pd.to_timedelta((t - excess_time), unit='D')
        return date

    def sim(self, time, excess_time=None, start_date='2020-03-15', N=1, draw_fcn=None, samples=None):
        """
        Run a model simulation for the given time period. Can optionally perform N repeated simulations of time days.
        Can use samples drawn using MCMC to perform the repeated simulations.


        Parameters
        ----------
        time : int or list of int [start, stop]
            The start and stop time for the simulation run.
            If an int is specified, it is interpreted as [0, time].

        N : int
            Number of repeated simulations. One by default.

        draw_fcn : function
            A function which takes as its input the dictionary of model parameters and the dictionary of sampled parameter values and assings these samples to the model parameter dictionary ad random.
            # TO DO: verify draw_fcn

        samples : dictionary
            Sample dictionary used by draw_fcn.
            # TO DO: should not be included if draw_fcn is not None. How can this be made more elegant?

        Returns
        -------
        xarray.Dataset

        """

        if isinstance(time, int):
            time = [0, time]

        if isinstance(time, str):
            time = [0, self.date_to_diff(start_date, time, excess_time)]

        # Copy parameter dictionary --> dict is global
        cp = copy.deepcopy(self.parameters)
        # Perform first simulation as preallocation
        if draw_fcn:
            self.parameters = draw_fcn(self.parameters,samples)
        out = self._sim_single(time, start_date, excess_time)
        # Repeat N - 1 times and concatenate
        for _ in range(N-1):
            if draw_fcn:
                self.parameters = draw_fcn(self.parameters,samples)
            out = xarray.concat([out, self._sim_single(time, start_date, excess_time)], "draws")

        # Reset parameter dictionary
        self.parameters = cp

        return out

    def _output_to_xarray_dataset(self, output):
        """
        Convert array (returned by scipy) to an xarray Dataset with variable names
        """

        if self.stratification:
            dims = self.stratification.copy()
        else:
            dims = []
        dims.append('time')

        coords = {
            "time": output["t"],
        }

        if self.stratification:
            for i in range(len(self.stratification)):
                if self.coordinates and self.coordinates[i] is not None:
                    coords.update({self.stratification[i]: self.coordinates[i]})
                else:
                    coords.update({self.stratification[i]: np.arange(self.stratification_size[i])})

        size_lst = [len(self.state_names)]
        if self.stratification:
            for size in self.stratification_size:
                size_lst.append(size)
        size_lst.append(len(output["t"]))
        y_reshaped = output["y"].reshape(tuple(size_lst))

        data = {}
        for var, arr in zip(self.state_names, y_reshaped):
            xarr = xarray.DataArray(arr, coords=coords, dims=dims)
            data[var] = xarr

        attrs = {'parameters': dict(self.parameters)}
        return xarray.Dataset(data, attrs=attrs)
