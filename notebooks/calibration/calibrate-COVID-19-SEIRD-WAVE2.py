"""
This script contains a four-prevention parameter, two-parameter delayed compliance ramp calibration to hospitalization data from the first COVID-19 wave in Belgium.
Deterministic, national-level BIOMATH COVID-19 SEIRD
Its intended use is the calibration for the descriptive manuscript: "A deterministic, age-stratified, extended SEIRD model for investigating the effect of non-pharmaceutical interventions on SARS-CoV-2 spread in Belgium".
"""

__author__      = "Tijs Alleman"
__copyright__   = "Copyright (c) 2020 by T.W. Alleman, BIOMATH, Ghent University. All Rights Reserved."

# ----------------------
# Load required packages
# ----------------------
import gc
import sys, getopt
import ujson as json
import random
import emcee
import datetime
import corner
import argparse
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import multiprocessing as mp
from multiprocessing import Pool
from covid19model.models import models
from covid19model.optimization.run_optimization import checkplots, calculate_R0
from covid19model.optimization.objective_fcns import prior_custom, prior_uniform
from covid19model.data import mobility, sciensano, model_parameters
from covid19model.optimization import pso, objective_fcns
from covid19model.models.time_dependant_parameter_fncs import ramp_fun
from covid19model.visualization.output import _apply_tick_locator 
from covid19model.visualization.optimization import autocorrelation_plot, traceplot

# -----------------------
# Handle script arguments
# -----------------------

parser = argparse.ArgumentParser()
parser.add_argument("-b", "--backend", help="Initiate MCMC backend", action="store_true")
parser.add_argument("-j", "--job", help="Full or partial calibration")
parser.add_argument("-d", "--date", help="Calibration date beta (to be used with --job COMPLIANCE)")

args = parser.parse_args()

# Backend
if args.backend == False:
    backend = None
else:
    backend = True

# Job type
if args.job:
    job = str(args.job)  
    if job not in ['BETA','COMPLIANCE']:
        raise ValueError(
            'Illegal job argument. Valid arguments are: "BETA" or "COMPLIANCE"'
        )     
    if job == 'COMPLIANCE':
        if args.date:
            date=str(args.date)
        else:
            raise ValueError(
                'Job "COMPLIANCE" requires the defenition of the calibration date of BETA!'
            )
else:
    job = None

# Date at which script is started
run_date = str(datetime.date.today())

# ---------
# Load data
# ---------

# Contact matrices
initN, Nc_home, Nc_work, Nc_schools, Nc_transport, Nc_leisure, Nc_others, Nc_total = model_parameters.get_interaction_matrices(dataset='willem_2012')
Nc_all = {'total': Nc_total, 'home':Nc_home, 'work': Nc_work, 'schools': Nc_schools, 'transport': Nc_transport, 'leisure': Nc_leisure, 'others': Nc_others}
levels = initN.size
# Sciensano data
df_sciensano = sciensano.get_sciensano_COVID19_data(update=False)
# Google Mobility data
df_google = mobility.get_google_mobility_data(update=False)
# Model initial condition on September 1st
warmup = 0
with open('../../data/interim/model_parameters/COVID19_SEIRD/calibrations/national/initial_states_2020-09-01.json', 'r') as fp:
    initial_states = json.load(fp)    

initial_states.update({
    'V': np.zeros(9),
    'V_new': np.zeros(9),
    'alpha': np.zeros(9)
})
initial_states['ICU_tot'] = initial_states.pop('ICU')

# ------------------------
# Define results locations
# ------------------------

# Path where samples bakcend should be stored
results_folder = "../../results/calibrations/COVID19_SEIRD/national/backends/"
# Path where figures should be stored
fig_path = '../../results/calibrations/COVID19_SEIRD/national/'
# Path where MCMC samples should be saved
samples_path = '../../data/interim/model_parameters/COVID19_SEIRD/calibrations/national/'

# ---------------------------------
# Time-dependant parameter function
# ---------------------------------

# Extract build contact matrix function
from covid19model.models.time_dependant_parameter_fncs import make_contact_matrix_function, ramp_fun
contact_matrix_4prev, all_contact, all_contact_no_schools = make_contact_matrix_function(df_google, Nc_all)

# Define policy function
def policies_wave1_4prev(t, param, l , tau, prev_schools, prev_work, prev_rest, prev_home):
    
    # Convert tau and l to dates
    tau_days = pd.Timedelta(tau, unit='D')
    l_days = pd.Timedelta(l, unit='D')

    # Define key dates of first wave
    t1 = pd.Timestamp('2020-03-15') # start of lockdown
    t2 = pd.Timestamp('2020-05-15') # gradual re-opening of schools (assume 50% of nominal scenario)
    t3 = pd.Timestamp('2020-07-01') # start of summer holidays
    t4 = pd.Timestamp('2020-09-01') # end of summer holidays

    # Define key dates of second wave
    t5 = pd.Timestamp('2020-10-19') # lockdown (1)
    t6 = pd.Timestamp('2020-11-02') # lockdown (2)
    t7 = pd.Timestamp('2020-11-16') # schools re-open
    t8 = pd.Timestamp('2020-12-18') # Christmas holiday starts
    t9 = pd.Timestamp('2021-01-04') # Christmas holiday ends
    t10 = pd.Timestamp('2021-02-15') # Spring break starts
    t11 = pd.Timestamp('2021-02-21') # Spring break ends
    t12 = pd.Timestamp('2021-04-05') # Easter holiday starts
    t13 = pd.Timestamp('2021-04-18') # Easter holiday ends

    t = pd.Timestamp(t.date())
    # First wave
    if t <= t1:
        return all_contact(t)
    elif t1 < t < t1 + tau_days:
        return all_contact(t)
    elif t1 + tau_days < t <= t1 + tau_days + l_days:
        policy_old = all_contact(t)
        policy_new = contact_matrix_4prev(t, prev_home, prev_schools, prev_work, prev_rest, 
                                    school=0)
        return ramp_fun(policy_old, policy_new, t, tau_days, l, t1)
    elif t1 + tau_days + l_days < t <= t2:
        return contact_matrix_4prev(t, prev_home, prev_schools, prev_work, prev_rest, 
                              school=0)
    elif t2 < t <= t3:
        return contact_matrix_4prev(t, prev_home, prev_schools, prev_work, prev_rest, 
                              school=0)
    elif t3 < t <= t4:
        return contact_matrix_4prev(t, school=0)
    # Second wave
    elif t4 < t <= t5 + tau_days:
        return contact_matrix_4prev(t, school=1)
    elif t5 + tau_days < t <= t5 + tau_days + l_days:
        policy_old = contact_matrix_4prev(t, school=1)
        policy_new = contact_matrix_4prev(t, prev_schools, prev_work, prev_rest, 
                                    school=1)
        return ramp_fun(policy_old, policy_new, t, tau_days, l, t5)
    elif t5 + tau_days + l_days < t <= t6:
        return contact_matrix_4prev(t, prev_home, prev_schools, prev_work, prev_rest, 
                              school=1)
    elif t6 < t <= t7:
        return contact_matrix_4prev(t, prev_home, prev_schools, prev_work, prev_rest, 
                              school=0)
    elif t7 < t <= t8:
        return contact_matrix_4prev(t, prev_home, prev_schools, prev_work, prev_rest, 
                              school=1) 
    elif t8 < t <= t9:
        return contact_matrix_4prev(t, prev_home, prev_schools, prev_work, prev_rest, 
                              school=0)
    elif t9 < t <= t10:
        return contact_matrix_4prev(t, prev_home, prev_schools, prev_work, prev_rest, 
                              school=1)
    elif t10 < t <= t11:
        return contact_matrix_4prev(t, prev_home, prev_schools, prev_work, prev_rest, 
                              school=0)    
    elif t11 < t <= t12:
        return contact_matrix_4prev(t, prev_home, prev_schools, prev_work, prev_rest, 
                              school=1)
    elif t12 < t <= t13:
        return contact_matrix_4prev(t, prev_home, prev_schools, prev_work, prev_rest, 
                              school=0)                                                                                                                                                     
    else:
        t = pd.Timestamp(t.date())
        return contact_matrix_4prev(t, prev_home, prev_schools, prev_work, prev_rest, 
                              school=1)

#############################
## PART 1: BETA AND WARMUP ##
#############################

# --------------------
# Calibration settings
# --------------------

# Start of data collection
start_data = '2020-03-15'
# Start data of recalibration ramp
start_calibration = '2020-09-30'
# Last datapoint used to calibrate warmup and beta
end_calibration_beta = '2020-10-23'
# Spatial unit: Belgium
spatial_unit = 'BE_WAVE2'
# PSO settings
processes = mp.cpu_count()
multiplier = 5
maxiter = 20
popsize = multiplier*processes
# MCMC settings
max_n = 300000
# Number of samples used to visualise model fit
n_samples = 100
# Confidence level used to visualise model fit
conf_int = 0.05
# Number of binomial draws per sample drawn used to visualize model fit
n_draws_per_sample=100

# --------------------
# Initialize the model
# --------------------

# Load the model parameters dictionary
params = model_parameters.get_COVID19_SEIRD_parameters()
# Add the time-dependant parameter function arguments
params.update({'l': 21, 'tau': 21, 'prev_schools': 0, 'prev_work': 0.5, 'prev_rest': 0.5, 'prev_home': 0.5})
# Initialize model
model = models.COVID19_SEIRD(initial_states, params,
                        time_dependent_parameters={'Nc': policies_wave1_4prev})
# Samples dict of WAVE1
samples_dict_WAVE1 = json.load(open(samples_path+'BE_WAVE1_BETA_COMPLIANCE_2021-02-22.json'))

if job == None or job == 'BETA':

    print('\n-----------------------------------------')
    print('PERFORMING CALIBRATION OF BETA AND WARMUP')
    print('-----------------------------------------\n')
    print('Using data from '+start_calibration+' until '+end_calibration_beta+'\n')
    print('1) Particle swarm optimization\n')
    print('Using ' + str(processes) + ' cores\n')

    # define dataset
    data=[df_sciensano['H_in'][start_calibration:end_calibration_beta]]
    states = [["H_in"]]

    # ------------------------
    # Define sampling function
    # ------------------------

    samples_dict = {}
    # Set up a draw function that doesn't keep track of sampled parameters not equal to calibrated parameter for PSO
    def draw_fcn(param_dict,samples_dict):
        param_dict['sigma'] = 5.2 - param_dict['omega']
        return param_dict

    # set PSO optimisation settings
    parNames = ['warmup','beta','omega','da']
    bounds=((5,30),(0.010,0.100),(0.1,2.0),(3,8))

    # run PSO optimisation
    #theta = pso.fit_pso(model,data,parNames,states,bounds,maxiter=maxiter,popsize=popsize,
    #                    start_date=start_calibration, processes=processes,draw_fcn=draw_fcn, samples={})
    theta = np.array([26.49300974, 0.0277392, 1.54274339, 4.78543434]) #-25299.093816290682

    warmup = int(theta[0])
    model.parameters['beta'] = theta[1]
    model.parameters['omega'] = theta[2]
    model.parameters['da'] = theta[3]

    # Visualise the PSO fit
    start_sim = start_calibration
    end_sim = '2020-10-31'
    out = model.sim(end_sim,start_date=start_sim,warmup=warmup,draw_fcn=draw_fcn,samples={})

    # Plot
    fig,ax = plt.subplots(figsize=(10,5))
    # Incidence
    ax.plot(out['time'],out['H_in'].sum(dim='Nc'),'--', color='blue')
    ax.scatter(df_sciensano[start_calibration:end_calibration_beta].index,df_sciensano['H_in'][start_calibration:end_calibration_beta], color='black', alpha=0.6, linestyle='None', facecolors='none', s=60, linewidth=2)
    ax.scatter(df_sciensano[pd.to_datetime(end_calibration_beta)+datetime.timedelta(days=1):end_sim].index,df_sciensano['H_in'][pd.to_datetime(end_calibration_beta)+datetime.timedelta(days=1):end_sim], color='red', alpha=0.6, linestyle='None', facecolors='none', s=60, linewidth=2)
    ax = _apply_tick_locator(ax)
    ax.set_xlim(start_sim,end_sim)
    ax.set_ylabel('$H_{in}$ (-)')
    plt.show()

    # run MCMC sampler
    print('\n2) Markov-Chain Monte-Carlo sampling\n')

    # Setup parameter names, bounds, number of chains, etc.
    parNames_mcmc = ['beta','omega','da']
    ndim = len(parNames_mcmc)
    nwalkers = ndim*4
    log_prior_fnc = [prior_uniform, prior_uniform, prior_uniform]
    log_prior_fnc_args = [(0.005, 0.15),(0.1, 5.1),(0.1, 14)]

    # Perturbate PSO Estimate
    pos = np.zeros([nwalkers,ndim])
    # Beta
    pos[:,0] = theta[1] + theta[1]*5e-2*np.random.uniform(low=-1,high=1,size=(nwalkers))
    # Omega and da
    pos[:,1:3] = theta[2:] + theta[2:]*1e-1*np.random.uniform(low=-1,high=1,size=(nwalkers,2))

    # Set up the sampler backend
    if backend:
        filename = spatial_unit+'_BETA_'+run_date
        backend = emcee.backends.HDFBackend(results_folder+filename)
        backend.reset(nwalkers, ndim)

    # Run sampler
    # We'll track how the average autocorrelation time estimate changes
    index = 0
    autocorr = np.empty(max_n)
    # This will be useful to testing convergence
    old_tau = np.inf
    # Initialize autocorr vector and autocorrelation figure
    autocorr = np.zeros([1,ndim])
    
    def draw_fcn(param_dict,samples_dict):
        param_dict['sigma'] = 5.2 - param_dict['omega']
        return param_dict

    with Pool() as pool:
        sampler = emcee.EnsembleSampler(nwalkers, ndim, objective_fcns.log_probability,backend=backend,pool=pool,
                        args=(model, log_prior_fnc, log_prior_fnc_args, data, states, parNames_mcmc, draw_fcn, {}, start_calibration, warmup,'poisson'))
        for sample in sampler.sample(pos, iterations=max_n, progress=True, store=True):
            # Only check convergence every 10 steps
            if sampler.iteration % 100:
                continue
            
            ##################
            # UPDATE FIGURES #
            ################## 

            # Compute the autocorrelation time so far
            tau = sampler.get_autocorr_time(tol=0)
            autocorr = np.append(autocorr,np.transpose(np.expand_dims(tau,axis=1)),axis=0)
            index += 1

            # Update autocorrelation plot
            n = 100 * np.arange(0, index + 1)
            y = autocorr[:index+1,:]
            fig,ax = plt.subplots(figsize=(10,5))
            ax.plot(n, n / 50.0, "--k")
            ax.plot(n, y, linewidth=2,color='red')
            ax.set_xlim(0, n.max())
            ax.set_ylim(0, y.max() + 0.1 * (y.max() - y.min()))
            ax.set_xlabel("number of steps")
            ax.set_ylabel(r"integrated autocorrelation time $(\hat{\tau})$")
            fig.savefig(fig_path+'autocorrelation/'+spatial_unit+'_AUTOCORR_BETA_'+run_date+'.pdf', dpi=400, bbox_inches='tight')
            
            # Update traceplot
            traceplot(sampler.get_chain(),['$\\beta$','$\\omega$','$d_{a}$'],
                            filename=fig_path+'traceplots/'+spatial_unit+'_TRACE_BETA_'+run_date+'.pdf',
                            plt_kwargs={'linewidth':2,'color': 'red','alpha': 0.15})

            plt.close('all')
            gc.collect()

            #####################
            # CHECK CONVERGENCE #
            ##################### 

            # Check convergence using mean tau
            converged = np.all(np.mean(tau) * 50 < sampler.iteration)
            converged &= np.all(np.abs(np.mean(old_tau) - np.mean(tau)) / np.mean(tau) < 0.03)
            if converged:
                break
            old_tau = tau

            ###############################
            # WRITE SAMPLES TO DICTIONARY #
            ###############################

            # Write samples to dictionary every 200 steps
            if sampler.iteration % 100: 
                continue

            flat_samples = sampler.get_chain(flat=True)
            with open(samples_path+str(spatial_unit)+'_BETA_'+run_date+'.npy', 'wb') as f:
                np.save(f,flat_samples)
                f.close()
                gc.collect()

    thin = 1
    try:
        autocorr = sampler.get_autocorr_time()
        thin = int(0.5 * np.min(autocorr))
    except:
        print('Warning: The chain is shorter than 50 times the integrated autocorrelation time.\nUse this estimate with caution and run a longer chain!\n')

    checkplots(sampler, int(2 * np.min(autocorr)), thin, fig_path, spatial_unit, figname='BETA', labels=['$\\beta$','$\\omega$','$d_{a}$'])

    print('\n3) Sending samples to dictionary')

    flat_samples = sampler.get_chain(discard=100,thin=thin,flat=True)
    samples_dict = {}
    for count,name in enumerate(parNames_mcmc):
        samples_dict[name] = flat_samples[:,count].tolist()

    samples_dict.update({
        'warmup' : warmup,
        'start_date_beta' : start_calibration,
        'end_date_beta' : end_calibration_beta,
        'n_chains_beta': int(nwalkers)
    })

    # ------------------------
    # Define sampling function
    # ------------------------

    def draw_fcn(param_dict,samples_dict):
        idx, param_dict['beta'] = random.choice(list(enumerate(samples_dict['beta'])))
        model.parameters['da'] = samples_dict['da'][idx]
        model.parameters['omega'] = samples_dict['omega'][idx]
        model.parameters['sigma'] = 5.2 - samples_dict['omega'][idx]
        return param_dict

    # ----------------------
    # Perform sampling
    # ----------------------

    print('4) Simulating using sampled parameters')
    start_sim = start_calibration
    end_sim = '2020-11-10'
    out = model.sim(end_sim,start_date=start_sim,warmup=warmup,N=n_samples,draw_fcn=draw_fcn,samples=samples_dict)

    # ---------------------------
    # Adding binomial uncertainty
    # ---------------------------

    print('5) Adding binomial uncertainty')

    LL = conf_int/2
    UL = 1-conf_int/2

    H_in = out["H_in"].sum(dim="Nc").values
    # Initialize vectors
    H_in_new = np.zeros((H_in.shape[1],n_draws_per_sample*n_samples))
    # Loop over dimension draws
    for n in range(H_in.shape[0]):
        binomial_draw = np.random.poisson( np.expand_dims(H_in[n,:],axis=1),size = (H_in.shape[1],n_draws_per_sample))
        H_in_new[:,n*n_draws_per_sample:(n+1)*n_draws_per_sample] = binomial_draw
    # Compute mean and median
    H_in_mean = np.mean(H_in_new,axis=1)
    H_in_median = np.median(H_in_new,axis=1)
    # Compute quantiles
    H_in_LL = np.quantile(H_in_new, q = LL, axis = 1)
    H_in_UL = np.quantile(H_in_new, q = UL, axis = 1)

    # -----------
    # Visualizing
    # -----------

    print('6) Visualizing fit \n')

    # Plot
    fig,ax = plt.subplots(figsize=(10,5))
    # Incidence
    ax.fill_between(pd.to_datetime(out['time'].values),H_in_LL, H_in_UL,alpha=0.20, color = 'blue')
    ax.plot(out['time'],H_in_mean,'--', color='blue')
    ax.scatter(df_sciensano[start_calibration:end_calibration_beta].index,df_sciensano['H_in'][start_calibration:end_calibration_beta], color='black', alpha=0.6, linestyle='None', facecolors='none', s=60, linewidth=2)
    ax.scatter(df_sciensano[pd.to_datetime(end_calibration_beta)+datetime.timedelta(days=1):end_sim].index,df_sciensano['H_in'][pd.to_datetime(end_calibration_beta)+datetime.timedelta(days=1):end_sim], color='red', alpha=0.6, linestyle='None', facecolors='none', s=60, linewidth=2)
    ax = _apply_tick_locator(ax)
    ax.set_xlim(start_sim,end_sim)
    ax.set_ylabel('$H_{in}$ (-)')
    fig.savefig(fig_path+'others/'+spatial_unit+'_FIT_BETA_'+run_date+'.pdf', dpi=400, bbox_inches='tight')

    #############################################
    ####### CALCULATING R0 ######################
    #############################################


    print('-----------------------------------')
    print('COMPUTING BASIC REPRODUCTION NUMBER')
    print('-----------------------------------\n')

    print('1) Computing')

    R0, R0_stratified_dict = calculate_R0(samples_dict, model, initN, Nc_total)

    print('2) Sending samples to dictionary')

    samples_dict.update({
        'R0': R0,
        'R0_stratified_dict': R0_stratified_dict,
    })

    print('3) Saving dictionary\n')

    with open(samples_path+str(spatial_unit)+'_BETA_'+run_date+'.json', 'w') as fp:
        json.dump(samples_dict, fp)

    print('DONE!')
    print('SAMPLES DICTIONARY SAVED IN '+'"'+samples_path+str(spatial_unit)+'_BETA_'+run_date+'.json'+'"')
    print('-----------------------------------------------------------------------------------------------------------------------------------\n')
    
    if job == 'BETA':
        sys.exit()

elif job == 'COMPLIANCE':
    samples_dict = json.load(open(samples_path+str(spatial_unit)+'_BETA_'+date+'.json'))
    warmup = int(samples_dict['warmup'])

############################################
## PART 2: COMPLIANCE RAMP AND PREVENTION ##
############################################

# --------------------
# Calibration settings
# --------------------

# Start of data collection
start_data = '2020-03-15'
# Start of calibration
start_calibration = '2020-09-30'
# Last datapoint used to calibrate compliance and prevention
end_calibration = '2021-02-07'
# PSO settings
processes = mp.cpu_count()
multiplier = 3
maxiter = 100
popsize = multiplier*processes
# MCMC settings
max_n = 500000
# Number of samples used to visualise model fit
n_samples = 200
# Confidence level used to visualise model fit
conf_int = 0.05
# Number of binomial draws per sample drawn used to visualize model fit
n_draws_per_sample=100

print('\n---------------------------------------------------')
print('PERFORMING CALIBRATION OF COMPLIANCE AND PREVENTION')
print('---------------------------------------------------\n')
print('Using data from '+start_calibration+' until '+end_calibration+'\n')
print('\n1) Markov-Chain Monte-Carlo sampling\n')
print('Using ' + str(processes) + ' cores\n')

# --------------
# Define dataset
# --------------

data=[df_sciensano['H_in'][start_calibration:end_calibration]]
states = [["H_in"]]

# ----------------
# PSO optimization
# ----------------

# set PSO optimisation settings
parNames = ['beta','omega','da','l', 'tau', 'prev_schools', 'prev_work', 'prev_rest', 'prev_home']
bounds=((0.02,0.09),(0.1,2),(3,6),(4,10),(0.1,2),(0.05,1.3),(0.05,1),(0.05,1),(0.05,1.3))

# run PSO optimisation
theta = pso.fit_pso(model, data, parNames, states, bounds, maxiter=maxiter, popsize=popsize,
                    start_date=start_calibration, warmup=warmup, processes=processes,
                    draw_fcn=None, samples=None)
# Calibration until 2021-02-01
#theta = np.array([0.02520874, 0.5908867, 7.54873678, 3.16858683, 0.22840117, 0.99, 0.09266227, 0.76026119, 0.62815982]) #-153561.23285318824
# [0.02751193 1.         5.16157345 6.76398559 0.90783608 2.
#  0.68541914 0.05285296 1.25573921] -159553.78248521767

# Calibration until 2020-11-16
#theta = np.array([0.04463705, 0.13275703, 3.35365264, 9.71239977, 0.1, 0.87725368, 0.91702074, 0.41036883, 0.09275467]) # -99028.25703611995
#theta = np.array([0.05230687, 0.47126374, 2.07289006, 0.1, 5, 0.47877545, 0.88442966, 0.46150047, 0.99]) # -99035.92564359006
# Calibration until 2020-12-18
#theta = np.array([0.03126882, 0.74213375, 4.3947722,  0.60772632, 3.80987428, 0.9725074, 0.97767155, 0.23587966, 0.7530272]) #-129997.88991388638

model.parameters['beta'] = theta[0]
model.parameters['omega'] = theta[1]
model.parameters['da'] = theta[2]
model.parameters['l'] = theta[3]
model.parameters['tau'] = theta[4]
model.parameters['prev_schools'] = theta[5]
model.parameters['prev_work'] = theta[6]
model.parameters['prev_rest'] = theta[7]
model.parameters['prev_home'] =  theta[8]

def draw_fcn(param_dict,samples_dict):
    param_dict['sigma'] = 5.2 - param_dict['omega']
    return param_dict

start_sim = start_calibration
end_sim = '2021-04-01'
out = model.sim(end_sim,start_date=start_sim,warmup=warmup,draw_fcn=draw_fcn,samples={})

# Plot
fig,ax = plt.subplots(figsize=(10,5))
# Incidence
ax.plot(out['time'],out['H_in'].sum(dim='Nc'),'--', color='blue')
ax.scatter(df_sciensano[start_calibration:end_calibration].index,df_sciensano['H_in'][start_calibration:end_calibration], color='black', alpha=0.6, linestyle='None', facecolors='none', s=60, linewidth=2)
ax.scatter(df_sciensano[pd.to_datetime(end_calibration)+datetime.timedelta(days=1):end_sim].index,df_sciensano['H_in'][pd.to_datetime(end_calibration)+datetime.timedelta(days=1):end_sim], color='red', alpha=0.6, linestyle='None', facecolors='none', s=60, linewidth=2)
ax = _apply_tick_locator(ax)
ax.set_xlim(start_sim,end_sim)
ax.set_ylabel('$H_{in}$ (-)')
plt.show()

# ------------
# MCMC sampler
# ------------

# Prior beta
density_beta, bins_beta = np.histogram(samples_dict['beta'], bins=20, density=True)
density_beta_norm = density_beta/np.sum(density_beta)

# Prior omega
density_omega, bins_omega = np.histogram(samples_dict['omega'], bins=20, density=True)
density_omega_norm = density_omega/np.sum(density_omega)

#Prior da
density_da, bins_da = np.histogram(samples_dict['da'], bins=20, density=True)
density_da_norm = density_da/np.sum(density_da)

# Setup parameter names, bounds, number of chains, etc.
parNames_mcmc = ['beta','omega','da','l', 'tau', 'prev_schools', 'prev_work', 'prev_rest', 'prev_home']
log_prior_fnc = [prior_uniform, prior_uniform, prior_uniform, prior_uniform, prior_uniform, prior_uniform, prior_uniform, prior_uniform, prior_uniform]
log_prior_fnc_args = [(0.005, 0.15),(0.1, 5.1),(0.1, 14),(0.001,20), (0.001,20), (0,1), (0,1), (0,1), (0,1)]
ndim = len(parNames_mcmc)
nwalkers = ndim*4
# Perturbate PSO Estimate
pos = np.zeros([nwalkers,ndim])
# Beta
pos[:,0] = theta[0] + theta[0]*1e-2*np.random.uniform(low=-1,high=1,size=(nwalkers))
# Omega and da
pos[:,1:3] = theta[1:3] + theta[1:3]*1e-2*np.random.uniform(low=-1,high=1,size=(nwalkers,2))
# l and tau
pos[:,3:5] = theta[3:5] + theta[3:5]*1e-2*np.random.uniform(low=-1,high=1,size=(nwalkers,2))
# prevention schools
pos[:,5] = theta[5] + theta[5]*1e-2*np.random.uniform(low=-1,high=1,size=(nwalkers))
# other prevention
pos[:,6:] = theta[6:] + theta[6:]*1e-2*np.random.uniform(low=-1,high=1,size=(nwalkers,len(theta[6:])))

# Set up the sampler backend
if backend:
    filename = spatial_unit+'_COMPLIANCE_'+run_date
    backend = emcee.backends.HDFBackend(results_folder+filename)
    backend.reset(nwalkers, ndim)

# Run sampler
# We'll track how the average autocorrelation time estimate changes
index = 0
autocorr = np.empty(max_n)
# This will be useful to testing convergence
old_tau = np.inf
# Initialize autocorr vector and autocorrelation figure
autocorr = np.zeros([1,ndim])
# Initialize the labels
labels = ['beta','omega','da','l', 'tau', 'prev_schools', 'prev_work', 'prev_rest', 'prev_home']

def draw_fcn(param_dict,samples_dict):
    param_dict['sigma'] = 5.2 - param_dict['omega']
    return param_dict

with Pool() as pool:
    sampler = emcee.EnsembleSampler(nwalkers, ndim, objective_fcns.log_probability,backend=backend,pool=pool,
                    args=(model,log_prior_fnc, log_prior_fnc_args, data, states, parNames_mcmc, draw_fcn, {}, start_calibration, warmup,'poisson'))
    for sample in sampler.sample(pos, iterations=max_n, progress=True, store=True):
       
        if sampler.iteration % 100:
            continue

        ##################
        # UPDATE FIGURES #
        ################## 

        # Compute the autocorrelation time so far
        tau = sampler.get_autocorr_time(tol=0)
        autocorr = np.append(autocorr,np.transpose(np.expand_dims(tau,axis=1)),axis=0)
        index += 1

        # Update autocorrelation plot
        n = 100 * np.arange(0, index + 1)
        y = autocorr[:index+1,:]
        fig,ax = plt.subplots(figsize=(10,5))
        ax.plot(n, n / 50.0, "--k")
        ax.plot(n, y, linewidth=2,color='red')
        ax.set_xlim(0, n.max())
        try:
            ax.set_ylim(0, y.max() + 0.1 * (y.max() - y.min()))
        except:
            print('\n Could not set axis limits because autocorrelation is equal to infinity.\n')
            print('This most likely indicates your chains are completely stuck in their initial values.\n')
        ax.set_xlabel("number of steps")
        ax.set_ylabel(r"integrated autocorrelation time $(\hat{\tau})$")
        fig.savefig(fig_path+'autocorrelation/'+spatial_unit+'_AUTOCORR_COMPLIANCE_'+run_date+'.pdf', dpi=400, bbox_inches='tight')

        # Update traceplot
        traceplot(sampler.get_chain(),labels,
                        filename=fig_path+'traceplots/'+spatial_unit+'_TRACE_COMPLIANCE_'+run_date+'.pdf',
                        plt_kwargs={'linewidth':2,'color': 'red','alpha': 0.15})

        # Close all figures and collect garbage to avoid memory leaks
        plt.close('all')
        gc.collect()

        #####################
        # CHECK CONVERGENCE #
        ##################### 

        # Check convergence using mean tau
        converged = np.all(np.mean(tau) * 50 < sampler.iteration)
        converged &= np.all(np.abs(np.mean(old_tau) - np.mean(tau)) / np.mean(tau) < 0.03)
        if converged:
            break
        old_tau = tau

        ###############################
        # WRITE SAMPLES TO DICTIONARY #
        ###############################

        # Write samples to dictionary every 1000 steps
        if sampler.iteration % 100: 
            continue

        flat_samples = sampler.get_chain(flat=True)
        with open(samples_path+str(spatial_unit)+'_BETA_COMPLIANCE_'+run_date+'.npy', 'wb') as f:
            np.save(f,flat_samples)
            f.close()
            gc.collect()

thin = 1
try:
    autocorr = sampler.get_autocorr_time()
    thin = int(0.5 * np.min(autocorr))
except:
    print('Warning: The chain is shorter than 50 times the integrated autocorrelation time.\nUse this estimate with caution and run a longer chain!\n')

checkplots(sampler, int(5 * np.max(tau)), thin, fig_path, spatial_unit, figname='COMPLIANCE', 
           labels=['$\\beta$','$\\omega$','$d_{a}$','l','$\\tau$', '$G_{schools}$', '$G_{work}$', '$G_{rest}$', '$G_{home}$'])

print('\n3) Sending samples to dictionary')

flat_samples = sampler.get_chain(discard=200,thin=thin,flat=True)

for count,name in enumerate(parNames_mcmc):
    samples_dict.update({name: flat_samples[:,count].tolist()})

with open(samples_path+str(spatial_unit)+'_BETA_COMPLIANCE_'+run_date+'.json', 'w') as fp:
    json.dump(samples_dict, fp)

# ------------------------
# Define sampling function
# ------------------------

def draw_fcn(param_dict,samples_dict):
    # Sample first calibration
    idx, param_dict['beta'] = random.choice(list(enumerate(samples_dict['beta'])))
    param_dict['da'] = samples_dict['da'][idx]
    param_dict['omega'] = samples_dict['omega'][idx]
    param_dict['sigma'] = 5.2 - samples_dict['omega'][idx]
    # Sample second calibration
    param_dict['tau'] = samples_dict['tau'][idx] 
    param_dict['l'] = samples_dict['l'][idx] 
    param_dict['prev_schools'] = samples_dict['prev_schools'][idx]
    param_dict['prev_home'] = samples_dict['prev_home'][idx]      
    param_dict['prev_work'] = samples_dict['prev_work'][idx]       
    param_dict['prev_rest'] = samples_dict['prev_rest'][idx]      
    return param_dict

# ----------------------
# Perform sampling
# ----------------------

print('4) Simulating using sampled parameters')
start_sim = start_calibration
end_sim = '2021-06-01'
out = model.sim(end_sim,start_date=start_sim,warmup=warmup,N=n_samples,draw_fcn=draw_fcn,samples=samples_dict)

# ---------------------------
# Adding binomial uncertainty
# ---------------------------

print('5) Adding binomial uncertainty')

LL = conf_int/2
UL = 1-conf_int/2

H_in = out["H_in"].sum(dim="Nc").values
# Initialize vectors
H_in_new = np.zeros((H_in.shape[1],n_draws_per_sample*n_samples))
# Loop over dimension draws
for n in range(H_in.shape[0]):
    binomial_draw = np.random.poisson( np.expand_dims(H_in[n,:],axis=1),size = (H_in.shape[1],n_draws_per_sample))
    H_in_new[:,n*n_draws_per_sample:(n+1)*n_draws_per_sample] = binomial_draw
# Compute mean and median
H_in_mean = np.mean(H_in_new,axis=1)
H_in_median = np.median(H_in_new,axis=1)
# Compute quantiles
H_in_LL = np.quantile(H_in_new, q = LL, axis = 1)
H_in_UL = np.quantile(H_in_new, q = UL, axis = 1)

# -----------
# Visualizing
# -----------

print('6) Visualizing fit \n')

# Plot
fig,ax = plt.subplots(figsize=(10,5))
# Incidence
ax.fill_between(pd.to_datetime(out['time'].values),H_in_LL, H_in_UL,alpha=0.20, color = 'blue')
ax.plot(out['time'],H_in_mean,'--', color='blue')
ax.scatter(df_sciensano[start_calibration:end_calibration].index,df_sciensano['H_in'][start_calibration:end_calibration], color='black', alpha=0.6, linestyle='None', facecolors='none', s=60, linewidth=2)
ax.scatter(df_sciensano[pd.to_datetime(end_calibration)+datetime.timedelta(days=1):].index,df_sciensano['H_in'][pd.to_datetime(end_calibration)+datetime.timedelta(days=1):], color='red', alpha=0.6, linestyle='None', facecolors='none', s=60, linewidth=2)
ax = _apply_tick_locator(ax)
ax.set_xlim(start_calibration,end_sim)
fig.savefig(fig_path+'others/'+spatial_unit+'_FIT_COMPLIANCE_'+run_date+'.pdf', dpi=400, bbox_inches='tight')
