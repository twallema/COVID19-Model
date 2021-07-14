# Original implementation by Ryan S. Mcgee can be found using the following link: https://github.com/ryansmcgee/seirsplus
# Copyright (c) 2020 by T.W. Alleman, D. Van Hauwermeiren, BIOMATH, Ghent University. All Rights Reserved.

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import numpy as numpy
import numpy as np
import scipy as scipy
import scipy.integrate
import pandas as pd
import random
from random import choices
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import datetime
import copy
import multiprocessing
from pandas.plotting import register_matplotlib_converters
register_matplotlib_converters()

from .utils import stratify_beta # read_coordinates_nis, dens_dep
from ..optimization import pso

# set color schemes
#From Color Universal Design (CUD): https://jfly.uni-koeln.de/color/
orange = "#E69F00"
light_blue = "#56B4E9"
green = "#009E73"
yellow = "#F0E442"
blue = "#0072B2"
red = "#D55E00"
pink = "#CC79A7"
black = "#000000"
Okabe_Ito = (orange, light_blue, green, yellow, blue, red, pink, black)
plt.rcParams["axes.prop_cycle"] = matplotlib.cycler('color', Okabe_Ito)

# increase font sizes
# the code below is not wrong, but kinda annoying if you continuously import
# this model in a notebook using the load_ext magic
#multiplier = 1.5
#keys = ("font.size", )
#for key in keys:
#    plt.rcParams[key] *= multiplier
plt.rcParams["font.size"] = 15
plt.rcParams["lines.linewidth"] = 3


from .base import BaseModel

class simple_multivariant_SIR(BaseModel):
    """
    A minimal example of a SIR compartmental disease model with an implementation of transient multivariant dynamics
    Can be reduced to a single-variant SIR model by setting injection_ratio to zero.

    Parameters
    ----------
    To initialise the model, provide following inputs:

    states : dictionary
        contains the initial values of all non-zero model states
        e.g. {'S': N, 'E': np.ones(n_stratification)} with N being the total population and n_stratifications the number of stratified layers
        initialising zeros is thus not required

        S : susceptible
        I : infectious
        R : removed
        alpha : fraction of alternative COVID-19 variant

    parameters : dictionary
        containing the values of all parameters (both stratified and not)

        Non-stratified parameters
        -------------------------

        beta : probability of infection when encountering an infected person
        gamma : recovery rate (inverse of duration of infectiousness)
        injection_day : day at which injection_ratio of the new strain is introduced in the population
        injection_ratio : initial fraction of alternative variant

        Other parameters
        ----------------
        Nc : contact matrix between all age groups in stratification

    """

    # state variables and parameters
    state_names = ['S', 'I', 'R', 'alpha']
    parameter_names = ['beta', 'gamma', 'injection_day', 'injection_ratio']
    parameters_stratified_names = []
    stratification = ['Nc']

    @staticmethod
    def integrate(t, S, I, R, alpha, beta, gamma, injection_day, injection_ratio, K_inf, Nc):
        """Basic SIR model with multivariant capabilities"""

        # calculate total population
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~
        T = S + I + R

        # Compute infection pressure (IP) of both variants
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        IP_old = (1-alpha)*beta*np.matmul(Nc,(I/T))
        IP_new = alpha*K_inf*beta*np.matmul(Nc,(I/T))

        # Compute the  rates of change in every population compartment
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        dS = - (IP_old + IP_new)*S
        dI = (IP_old + IP_new)*S - gamma*I
        dR = gamma*I

        # Update fraction of new COVID-19 variant
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

        if np.all((IP_old == 0)) and np.all((IP_new == 0)):
            dalpha = np.zeros(9)
        else:
            dalpha = IP_new/(IP_old+IP_new) - alpha

        # On injection_day, inject injection_ratio new strain to alpha
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

        if (t >= injection_day) & (alpha.sum().sum()==0):
            dalpha += injection_ratio

        return dS, dI, dR, dalpha

class simple_stochastic_SIR(BaseModel):
    """
    A minimal example of a SIR compartmental disease model based on stochastic difference equations (SDEs)
    Must be simulated in discrete timesteps by adding the argument `discrete=True` in the model initialization.

    Parameters
    ----------
    To initialise the model, provide following inputs:

    states : dictionary
        contains the initial values of all non-zero model states
        e.g. {'S': N, 'E': np.ones(n_stratification)} with N being the total population and n_stratifications the number of stratified layers
        initialising zeros is thus not required

        S : susceptible
        I : infectious
        R : removed

    parameters : dictionary
        containing the values of all parameters (both stratified and not)

        Non-stratified parameters
        -------------------------
        beta : probability of infection when encountering an infected person
        gamma : recovery rate (inverse of duration of infectiousness)

        Other parameters
        ----------------
        Nc : contact matrix between all age groups in stratification

    """

    # state variables and parameters
    state_names = ['S', 'I', 'R']
    parameter_names = ['beta', 'gamma']
    parameters_stratified_names = []
    stratification = ['Nc']

    @staticmethod
    def integrate(t, S, I, R, beta, gamma, Nc):
        """Basic stochastic SIR model """

        # Define solver parameters
        # ~~~~~~~~~~~~~~~~~~~~~~~~

        l = 1.0 # length of discrete timestep (by default one day)
        n = 5 # number of draws to average in one timestep (slows down calculations but converges to a deterministic result when > 20)

        # calculate total population
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~

        T = S + I + R

        # Make a dictionary containing the transitions and their propensities
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

        keys = ['StoI','ItoR']
        probabilities = [1 - np.exp( - beta*np.matmul(Nc,I/T) ),
                        (1 - np.exp(- l * gamma ))*np.ones(S.size),
                        ]
        states = [S,I]
        propensity={}
        for i in range(len(keys)):
            prop=[]
            for j in range(S.size):
                if states[i][j]<=0:
                    prop.append(0)
                else:
                    draw=np.array([])
                    for l in range(n):
                        draw = np.append(draw,np.random.binomial(states[i][j],probabilities[i][j]))
                    draw = np.rint(np.mean(draw))
                    prop.append( draw )
            propensity.update({keys[i]: np.asarray(prop)})

        # calculate the states at timestep k+1
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        S_new  = S - propensity['StoI']
        I_new =  I + propensity['StoI'] - propensity['ItoR']
        R_new  =  R + propensity['ItoR']

        # Add protection against states < 0
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        output = (S_new, I_new, R_new)
        for i in range(len(output)):
            output[i][output[i]<0] = 0

        return S_new, I_new, R_new

class COVID19_SEIRD(BaseModel):
    """
    Biomath extended SEIRD model for COVID-19, Deterministic implementation
    Can account for re-infection and co-infection with a new COVID-19 variant.

    Parameters
    ----------
    To initialise the model, provide following inputs:

    states : dictionary
        contains the initial values of all non-zero model states
        e.g. {'S': N, 'E': np.ones(n_stratification)} with N being the total population and n_stratifications the number of stratified layers
        initialising zeros is thus not required

        S : susceptible
        E : exposed
        I : infected
        A : asymptomatic
        M : mild
        C : cohort
        C_icurec : cohort after recovery from ICU
        ICU : intensive care
        R : recovered
        D : deceased
        H_in : new hospitalizations
        H_out : new hospital discharges
        H_tot : total patients in Belgian hospitals

        parameters : dictionary
        containing the values of all parameters (both stratified and not)
        these can be obtained with the function model_parameters.get_COVID19_SEIRD_parameters()

        Non-stratified parameters
        -------------------------
        beta : probability of infection when encountering an infected person
        alpha : fraction of alternative COVID-19 variant
        K_inf1 : infectivity gain of B1.1.1.7 (British) COVID-19 variant (infectivity of new variant = K * infectivity of old variant)
        K_inf2 : infectivity gain of Indian COVID-19 variant
        # TODO: This is split because we have to estimate the infectivity gains, however, we should adjust the calibration code to allow estimation of subsets of vector parameters
        K_hosp : hospitalization propensity gain of alternative COVID-19 variants (infectivity of new variant = K * infectivity of old variant)
        sigma : length of the latent period
        omega : length of the pre-symptomatic infectious period
        zeta : effect of re-susceptibility and seasonality
        a : probability of an asymptomatic cases
        m : probability of an initially mild infection (m=1-a)
        da : duration of the infection in case of asymptomatic
        dm : duration of the infection in case of mild
        dc : average length of a hospital stay when not in ICU
        dICU_R : average length of a hospital stay in ICU in case of recovery
        dICU_D: average length of a hospital stay in ICU in case of death
        dhospital : time before a patient reaches the hospital

        Age-stratified parameters
        --------------------
        s: relative susceptibility to infection
        a : probability of a subclinical infection
        h : probability of hospitalisation for a mild infection
        c : probability of hospitalisation in Cohort (non-ICU)
        m_C : mortality in Cohort
        m_ICU : mortality in ICU

        Other parameters
        ----------------
        Nc : contact matrix between all age groups in stratification

    """

    # ...state variables and parameters
    state_names = ['S', 'E', 'I', 'A', 'M', 'C', 'C_icurec','ICU', 'R', 'D','H_in','H_out','H_tot','R_C','R_ICU']
    parameter_names = ['beta', 'sigma', 'omega', 'zeta','da', 'dm', 'dc_R','dc_D','dICU_R', 
                        'dICU_D', 'dICUrec','dhospital']
    parameters_stratified_names = [['s','a','h', 'c', 'm_C','m_ICU']]
    stratification = ['Nc']

    # ..transitions/equations
    @staticmethod
    def integrate(t, S, E, I, A, M, C, C_icurec, ICU, R, D, H_in, H_out, H_tot, R_C, R_ICU,
                  beta, sigma, omega, zeta, da, dm, dc_R, dc_D, dICU_R, dICU_D, dICUrec, dhospital,
                  s, a, h, c, m_C, m_ICU,
                  Nc):
        """
        Biomath extended SEIRD model for COVID-19

        *Deterministic implementation*
        """

        if Nc is None:
            print(t)

        # calculate total population
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~

        T = S + E + I + A + M + C + C_icurec + ICU + R

        # Compute infection pressure (IP) of both variants
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

        IP = beta*s*np.matmul(Nc,((I+A)/T))

        # Compute the  rates of change in every population compartment
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

        dS  = - IP*S + zeta*R 
        dE  = IP*S - E/sigma 
        dI = (1/sigma)*E - (1/omega)*I 
        dA = (a/omega)*I - A/da      
        dM = ((1-a)/omega)*I - M*((1-h)/dm) - M*h/dhospital

        dC = M*(h/dhospital)*c - (1-m_C)*C*(1/(dc_R)) - m_C*C*(1/(dc_D))
        dICUstar = M*(h/dhospital)*(1-c) - (1-m_ICU)*ICU/(dICU_R) - m_ICU*ICU/(dICU_D)

        dC_icurec = (1-m_ICU)*ICU/(dICU_R) - C_icurec*(1/dICUrec)
        dR  = A/da + ((1-h)/dm)*M + (1-m_C)*C*(1/(dc_R)) + C_icurec*(1/dICUrec) - zeta*R 
        dD  = (m_ICU/(dICU_D))*ICU + (m_C/(dc_D))*C 
        dH_in = M*(h/dhospital) - H_in
        dH_out =  (1-m_C)*C*(1/(dc_R)) +  m_C*C*(1/(dc_D)) + m_ICU/(dICU_D)*ICU + C_icurec*(1/dICUrec) - H_out
        dH_tot = M*(h/dhospital) - (1-m_C)*C*(1/(dc_R)) - m_C*C*(1/(dc_D)) - m_ICU*ICU/(dICU_D)- C_icurec*(1/dICUrec) 
        dR_C = (1-m_C)*C*(1/(dc_R)) - R_C
        dR_ICU = C_icurec*(1/dICUrec) - R_ICU

        return (dS, dE, dI, dA, dM, dC, dC_icurec, dICUstar, dR, dD, dH_in, dH_out, dH_tot, dR_C, dR_ICU)


class COVID19_SEIRD_stratified_vacc(BaseModel):
    """
    Biomath extended SEIRD model for COVID-19, Deterministic implementation
    Can account for re-infection and co-infection with a new COVID-19 variant.

    Parameters
    ----------
    To initialise the model, provide following inputs:

    states : dictionary
        contains the initial values of all non-zero model states
        e.g. {'S': N, 'E': np.ones(n_stratification)} with N being the total population and n_stratifications the number of stratified layers
        initialising zeros is thus not required

        S : susceptible
        E : exposed
        I : infected
        A : asymptomatic
        M : mild
        C : cohort
        C_icurec : cohort after recovery from ICU
        ICU : intensive care
        R : recovered
        D : deceased
        H_in : new hospitalizations
        H_out : new hospital discharges
        H_tot : total patients in Belgian hospitals

        parameters : dictionary
        containing the values of all parameters (both stratified and not)
        these can be obtained with the function model_parameters.get_COVID19_SEIRD_parameters()

        Non-stratified parameters
        -------------------------
        beta : probability of infection when encountering an infected person
        alpha : fraction of alternative COVID-19 variant
        K_inf1 : infectivity gain of B1.1.1.7 (British) COVID-19 variant (infectivity of new variant = K * infectivity of old variant)
        K_inf2 : infectivity gain of Indian COVID-19 variant
        # TODO: This is split because we have to estimate the infectivity gains, however, we should adjust the calibration code to allow estimation of subsets of vector parameters
        K_hosp : hospitalization propensity gain of alternative COVID-19 variants (infectivity of new variant = K * infectivity of old variant)
        sigma : length of the latent period
        omega : length of the pre-symptomatic infectious period
        zeta : effect of re-susceptibility and seasonality
        a : probability of an asymptomatic cases
        m : probability of an initially mild infection (m=1-a)
        da : duration of the infection in case of asymptomatic
        dm : duration of the infection in case of mild
        dc : average length of a hospital stay when not in ICU
        dICU_R : average length of a hospital stay in ICU in case of recovery
        dICU_D: average length of a hospital stay in ICU in case of death
        dhospital : time before a patient reaches the hospital

        Age-stratified parameters
        --------------------
        s: relative susceptibility to infection
        a : probability of a subclinical infection
        h : probability of hospitalisation for a mild infection
        c : probability of hospitalisation in Cohort (non-ICU)
        m_C : mortality in Cohort
        m_ICU : mortality in ICU

        Other parameters
        ----------------
        Nc : contact matrix between all age groups in stratification

    """

    # ...state variables and parameters
    state_names = ['S', 'E', 'I', 'A', 'M', 'C', 'C_icurec','ICU', 'R', 'D','H_in','H_out','H_tot']
    parameter_names = ['beta', 'alpha', 'K_inf1', 'K_inf2', 'K_hosp', 'sigma', 'omega', 'zeta','da', 'dm','dICUrec','dhospital','N_vacc', 'd_vacc', 'e_i', 'e_s', 'e_h']
    parameters_stratified_names = [['s','a','h', 'c', 'm_C','m_ICU', 'dc_R', 'dc_D','dICU_R','dICU_D'],[]]
    stratification = ['Nc','doses']

    # ..transitions/equations
    @staticmethod
    def integrate(t, S, E, I, A, M, C, C_icurec, ICU, R, D, H_in, H_out, H_tot,
                  beta, alpha, K_inf1, K_inf2, K_hosp, sigma, omega, zeta, da, dm,  dICUrec, dhospital, N_vacc, d_vacc, e_i, e_s, e_h,
                  s, a, h, c, m_C, m_ICU, dc_R, dc_D, dICU_R, dICU_D,
                  Nc, doses):
        """
        Biomath extended SEIRD model for COVID-19

        *Deterministic implementation*
        """
 
        K_inf = np.array([1, K_inf1, K_inf2])

        if Nc is None:
            print(t)

        # calculate total population
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~

        T = np.expand_dims(np.sum(S + E + I + A + M + C + C_icurec + ICU + R, axis=1),axis=1)

        # Account for higher hospitalisation propensity and changes in vaccination parameters due to new variant
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

        if sum(alpha) != 1:
            raise ValueError(
                "The sum of the fractions of the VOCs is not equal to one, please check your time dependant VOC function"
            )

        h = np.sum(np.outer(h, alpha*K_hosp),axis=1)
        if h[-1] > 1:
            h[-1] = 1
        e_i = np.matmul(alpha, e_i)
        e_s = np.matmul(alpha, e_s)
        e_h = np.matmul(alpha, e_h)

        # Expand dims on first stratification axis (age)
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

        a = np.expand_dims(a, axis=1)
        h = np.expand_dims(h, axis=1)
        c = np.expand_dims(c, axis=1)
        m_C = np.expand_dims(m_C, axis=1)
        m_ICU = np.expand_dims(m_ICU, axis=1)
        dc_R = np.expand_dims(dc_R, axis=1)
        dc_D = np.expand_dims(dc_D, axis=1)
        dICU_R = np.expand_dims(dICU_R, axis=1)
        dICU_D = np.expand_dims(dICU_D, axis=1)
        dICUrec = np.expand_dims(dICUrec, axis=1)

        # Compute infection pressure (IP) of all variants
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

        IP = np.expand_dims( np.sum( np.outer(beta*s*np.matmul(Nc,np.sum(((I+A)/T)*(1-e_i),axis=1)), alpha*K_inf) ,axis=1) , axis=1)

        # Compute the  rates of change in every population compartment
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

        dS  = - IP*S*(1-e_s)
        dE  = IP*S*(1-e_s) - E/sigma 
        dI = (1/sigma)*E - (1/omega)*I 
        dA = (a/omega)*I - A/da      
        dM = ((1-a)/omega)*I - M*((1-h)/dm) - (1-e_h)*M*h/dhospital

        dC = (1-e_h)*M*(h/dhospital)*c - (1-m_C)*C*(1/(dc_R)) - m_C*C*(1/(dc_D))
        dICUstar = M*(h/dhospital)*(1-c) - (1-m_ICU)*ICU/(dICU_R) - m_ICU*ICU/(dICU_D)

        dC_icurec = (1-m_ICU)*ICU/(dICU_R) - C_icurec*(1/dICUrec)
        dR  = A/da + ((1-h)/dm)*M + (1-m_C)*C*(1/(dc_R)) + C_icurec*(1/dICUrec)
        dD  = (m_ICU/(dICU_D))*ICU + (m_C/(dc_D))*C 
        dH_in = (1-e_h)*M*(h/dhospital) - H_in
        dH_out =  (1-m_C)*C*(1/(dc_R)) +  m_C*C*(1/(dc_D)) + m_ICU/(dICU_D)*ICU + C_icurec*(1/dICUrec) - H_out
        dH_tot = (1-e_h)*M*(h/dhospital) - (1-m_C)*C*(1/(dc_R)) - m_C*C*(1/(dc_D)) - m_ICU*ICU/(dICU_D)- C_icurec*(1/dICUrec) 


        # Compute the vaccination transitionings and waning of immunity
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

        # 0 doses --> 1 dose and 0 doses --> 2 doses
        VE = S[:,0] + R[:,0]
        f_S = S[:,0]/VE
        f_R = R[:,0]/VE
        dS[:,0] = dS[:,0] - N_vacc[:,0]*f_S -  N_vacc[:,2]*f_S 
        dR[:,0] = dR[:,0] - N_vacc[:,0]*f_R - N_vacc[:,2]*f_R
        dS[:,1] = dS[:,1] + N_vacc[:,0] # 0 --> 1 dose
        dS[:,2] = dS[:,2] + N_vacc[:,2] # 0 --> 2 doses
        # 1 dose --> 2 doses
        VE = S[:,1] + R[:,1]
        f_S = S[:,1]/VE
        f_R = R[:,1]/VE
        dS[:,1] = dS[:,1] - N_vacc[:,1]*f_S
        dR[:,1] = dR[:,1] - N_vacc[:,1]*f_R
        dS[:,2] = dS[:,2] + N_vacc[:,1]
        # Waning of vaccine immunity: first dose
        waning_S = (1/d_vacc)*S[:,1]
        waning_R = (1/d_vacc)*R[:,1]
        dS[:,0] = dS[:,0] + waning_S + waning_R
        dS[:,1] = dS[:,1] - waning_S
        dR[:,1] = dR[:,1] - waning_R
        # Waning of vaccine immunity: second dose
        waning_S = (1/d_vacc)*S[:,2]
        waning_R = (1/d_vacc)*R[:,2]
        dS[:,0] = dS[:,0] + waning_S + waning_R
        dS[:,2] = dS[:,2] - waning_S
        dR[:,2] = dR[:,2] - waning_R
        # Waning of natural immunity
        dS[:,0] = dS[:,0] + zeta*R[:,0] 
        dR[:,0] = dR[:,0] - zeta*R[:,0] 
        return (dS, dE, dI, dA, dM, dC, dC_icurec, dICUstar, dR, dD, dH_in, dH_out, dH_tot)


class COVID19_SEIRD_vacc(BaseModel):
    """
    Biomath extended SEIRD model for COVID-19, Deterministic implementation
    Can account for re-infection and co-infection with a new COVID-19 variants.
    Model compartments doubled for fundamental vaccination research.

    Parameters
    ----------
    To initialise the model, provide following inputs:

    states : dictionary
        contains the initial values of all non-zero model states
        e.g. {'S': N, 'E': np.ones(n_stratification)} with N being the total population and n_stratifications the number of stratified layers
        initialising zeros is thus not required

        S : susceptible
        E : exposed
        I : infected
        A : asymptomatic
        M : mild
        C : cohort
        C_icurec : cohort after recovery from ICU
        ICU : intensive care
        R : recovered
        D : deceased

        S_v : susceptible and vaccinated
        E_v : exposed and vaccinated
        I_v : infected and vaccinated
        A_v : asymptomatic and vaccinated
        M_v : mild and vaccinated
        C_v : cohort and vaccinated
        C_icurec_v : cohort after recovery from ICU and vaccinated
        ICU_v : intensive care and vaccinated
        R_v : recovered and vaccinated

        H_in : new hospitalizations
        H_out : new hospital discharges
        H_tot : total patients in Belgian hospitals

    parameters : dictionary
        containing the values of all parameters (both stratified and not)
        these can be obtained with the function model_parameters.get_COVID19_SEIRD_parameters()

        Non-stratified parameters
        -------------------------
        beta : probability of infection when encountering an infected person
        alpha : fraction of alternative COVID-19 variant
        K_inf1 : infectivity gain of B1.1.1.7 (British) COVID-19 variant (infectivity of new variant = K * infectivity of old variant)
        K_inf2 : infectivity gain of Indian COVID-19 variant
        # TODO: This is split because we have to estimate the infectivity gains, however, we should adjust the calibration code to allow estimation of subsets of vector parameters
        K_hosp : hospitalization propensity gain of alternative COVID-19 variants (infectivity of new variant = K * infectivity of old variant)
        sigma : length of the latent period
        omega : length of the pre-symptomatic infectious period
        zeta : effect of re-susceptibility and seasonality
        a : probability of an asymptomatic cases
        m : probability of an initially mild infection (m=1-a)
        da : duration of the infection in case of asymptomatic
        dm : duration of the infection in case of mild
        dc : average length of a hospital stay when not in ICU
        dICU_R : average length of a hospital stay in ICU in case of recovery
        dICU_D: average length of a hospital stay in ICU in case of death
        dhospital : time before a patient reaches the hospital
        e_i : vaccine effectiveness in reducing infectiousness (--> if vaccinated person becomes infectious, how infectious is he?)
        e_s : vaccine effectiveness in reducing susceptibility to SARS-CoV-2 infection
        e_h : vaccine effectivenes in reducing hospital admission propensity
        e_a : all-or-nothing vaccine effectiveness
        d_vacc : duration of vaccine protection

        Age-stratified parameters
        --------------------
        s: relative susceptibility to infection
        a : probability of a subclinical infection
        h : probability of hospitalisation for a mild infection
        c : probability of hospitalisation in Cohort (non-ICU)
        m_C : mortality in Cohort
        m_ICU : mortality in ICU
        N_vacc : number of people to be vaccinated per day

        Other parameters
        ----------------
        Nc : contact matrix between all age groups in stratification

    """

    # ...state variables and parameters
    state_names = ['S', 'E', 'I', 'A', 'M', 'C', 'C_icurec','ICU', 'R', 'D','H_in','H_out','H_tot', 'R_C', 'R_ICU',
                    'S_v', 'E_v', 'I_v', 'A_v', 'M_v', 'C_v', 'C_icurec_v', 'ICU_v', 'R_v']
    parameter_names = ['beta', 'alpha', 'K_inf1','K_inf2', 'K_hosp', 'sigma', 'omega', 'zeta','da', 'dm', 'dc_R','dc_D','dICU_R', 
                        'dICU_D', 'dICUrec','dhospital', 'e_i', 'e_s', 'e_h', 'e_a', 'd_vacc']
    parameters_stratified_names = [['s','a','h', 'c', 'm_C','m_ICU', 'N_vacc']]
    stratification = ['Nc']

    # ..transitions/equations
    @staticmethod
    def integrate(t, S, E, I, A, M, C, C_icurec, ICU, R, D, H_in, H_out, H_tot, R_C, R_ICU, S_v, E_v, I_v, A_v, M_v, C_v, C_icurec_v, ICU_v, R_v,
                  beta, alpha, K_inf1, K_inf2, K_hosp, sigma, omega, zeta, da, dm, dc_R, dc_D, dICU_R, dICU_D, dICUrec, dhospital, e_i, e_s, e_h, e_a, d_vacc,
                  s, a, h, c, m_C, m_ICU, N_vacc,
                  Nc):
        """
        Biomath extended SEIRD model for COVID-19

        *Deterministic implementation*
        """

        K_inf = np.array([1, K_inf1, K_inf2])

        # Print timestep of faulty social policy
        if Nc is None:
            print(t)

        # Calculate total population
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~

        T = S + E + I + A + M + C + C_icurec + ICU + R\
            + S_v + E_v + I_v + A_v + M_v + C_v + C_icurec_v + ICU_v + R_v

        # Compute the number of vaccine elegible individuals
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

        VE = S + R

        # Compute weighted average hospitalization propensity and vaccination parameters in accordance with variants
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

        if sum(alpha) != 1:
            raise ValueError(
                "The sum of the fractions of the VOCs is not equal to one, please check your time dependant VOC function"
            )
        # NOTE: This requires the sizes of alpha, K_hosp, K_inf, e_i, e_s, e_h, e_a to be consistent 
        # However, because this requires a for loop, we omit a test
        h = np.sum(np.outer(h, alpha*K_hosp),axis=1)
        e_i = np.sum(alpha*e_i)
        e_s = np.sum(alpha*e_s)
        e_h = np.sum(alpha*e_h)
        e_a = np.sum(alpha*e_a)

        # Compute infection pressure (IP) of both variants
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

        IP = np.sum(np.outer(beta*s*np.matmul(Nc,((I+A+(1-e_i)*(I_v+A_v))/T)), alpha*K_inf),axis=1)

        # Compute the  rates of change in every population compartment (non-vaccinated)
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

        dS  = - IP*S + zeta*R - e_a*N_vacc/VE*S + (1/d_vacc)*(S_v + R_v)  
        dE  = IP*S - E/sigma 
        dI = (1/sigma)*E - (1/omega)*I 
        dA = (a/omega)*I - A/da      
        dM = ((1-a)/omega)*I - M*((1-h)/dm) - M*h/dhospital
        dC = M*(h/dhospital)*c - (1-m_C)*C*(1/(dc_R)) - m_C*C*(1/(dc_D))
        dICUstar = M*(h/dhospital)*(1-c) - (1-m_ICU)*ICU/(dICU_R) - m_ICU*ICU/(dICU_D)
        dC_icurec = (1-m_ICU)*ICU/(dICU_R) - C_icurec*(1/dICUrec)
        dR  = A/da + ((1-h)/dm)*M + (1-m_C)*C*(1/(dc_R)) + C_icurec*(1/dICUrec) - zeta*R - e_a*N_vacc/VE*R

        # Compute the  rates of change in every population compartment (vaccinated)
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        
        dS_v  = - (1-e_s)*IP*S_v + e_a*N_vacc/VE*S + e_a*N_vacc/VE*R - (1/d_vacc)*S_v
        dE_v  = (1-e_s)*IP*S_v - E_v/sigma 
        dI_v = (1/sigma)*E_v - (1/omega)*I_v 
        dA_v = (a/omega)*I_v - A_v/da      
        dM_v = ((1-a)/omega)*I_v - M_v*((1-(1-e_h)*h)/dm) - M_v*(1-e_h)*h/dhospital
        dC_v = M_v*(1-e_h)*(h/dhospital)*c - (1-m_C)*C_v*(1/(dc_R)) - m_C*C_v*(1/(dc_D))
        dICUstar_v = M_v*(1-e_h)*(h/dhospital)*(1-c) - (1-m_ICU)*ICU_v/(dICU_R) - m_ICU*ICU_v/(dICU_D)
        dC_icurec_v = (1-m_ICU)*ICU_v/(dICU_R) - C_icurec_v*(1/dICUrec)
        dR_v  = A_v/da + ((1-(1-e_h)*h)/dm)*M_v + (1-m_C)*C_v*(1/dc_R) + C_icurec_v*(1/dICUrec) - (1/d_vacc)*R_v
        dD  = (m_ICU/dICU_D)*ICU + (m_C/dc_D)*C + (m_ICU/dICU_D)*ICU_v + (m_C/dc_D)*C_v

        # Compute the hospital rates of changes
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

        dH_in = M*(h/dhospital) + M_v*((1-e_h)*h/dhospital) - H_in
        dH_out =  (1-m_C)*C*(1/dc_R) +  m_C*C*(1/dc_D) + (m_ICU/dICU_D)*ICU + C_icurec*(1/dICUrec)\
            + (1-m_C)*C_v*(1/dc_R) +  m_C*C_v*(1/dc_D) + (m_ICU/dICU_D)*ICU_v + C_icurec_v*(1/dICUrec) - H_out
        dH_tot = M*(h/dhospital) - (1-m_C)*C*(1/dc_R) -  m_C*C*(1/dc_D) - (m_ICU/dICU_D)*ICU - C_icurec*(1/dICUrec)\
            + M_v*((1-e_h)*h/dhospital) - (1-m_C)*C_v*(1/dc_R) -  m_C*C_v*(1/dc_D) - (m_ICU/dICU_D)*ICU_v - C_icurec_v*(1/dICUrec)
        dR_C = (1-m_C)*C*(1/(dc_R)) + (1-m_C)*C_v*(1/dc_R) - R_C
        dR_ICU = C_icurec*(1/dICUrec) + C_icurec_v*(1/dICUrec)- R_ICU

        return (dS, dE, dI, dA, dM, dC, dC_icurec, dICUstar, dR, dD, dH_in, dH_out, dH_tot, dR_C, dR_ICU, dS_v, dE_v, dI_v, dA_v, dM_v, dC_v, dC_icurec_v, dICUstar_v, dR_v)


class COVID19_SEIRD_spatial(BaseModel):
    """
    BIOMATH extended SEIRD model for COVID-19, spatially explicit. Based on COVID_SEIRD and Arenas (2020).

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

        Non-stratified parameters
        -------------------------
        beta_R : probability of infection when encountering an infected person in rural environment
        beta_U : probability of infection when encountering an infected person in urban environment
        beta_M : probability of infection when encountering an infected person in metropolitan environment
        K : infectivity gain of alternative COVID-19 variants (infectivity of new variant = K * infectivity of old variant)
        sigma : length of the latent period
        omega : length of the pre-symptomatic infectious period
        zeta : effect of re-susceptibility and seasonality
        a : probability of an asymptomatic cases
        m : probability of an initially mild infection (m=1-a)
        da : duration of the infection in case of asymptomatic
        dm : duration of the infection in case of mild
        der : duration of stay in emergency room/buffer ward
        dc : average length of a hospital stay when not in ICU
        dICU_R : average length of a hospital stay in ICU in case of recovery
        dICU_D: average length of a hospital stay in ICU in case of death
        dhospital : time before a patient reaches the hospital
        xi : factor controlling the contact dependence on density f
        injection_day : number of days after start of simulation when new strain is injected
        injection_ratio : ratio of new strain vs total amount of virus on injection_day

        Age-stratified parameters
        -------------------------
        s: relative susceptibility to infection
        a : probability of a subclinical infection
        h : probability of hospitalisation for a mild infection
        c : probability of hospitalisation in Cohort (non-ICU)
        m_C : mortality in Cohort
        m_ICU : mortality in ICU
        pi : mobility parameter (1 by default = no measures)
        N_vacc : daily number of people vaccinated in each age group
        e : vaccine effectivity
        leakiness : leakiness of the vaccine (proportion of vaccinated people that contribute to infections)

        Spatially-stratified parameters
        -------------------------------
        place : normalised mobility data. place[g][h] denotes the fraction of the population in patch g that goes to patch h
        area : area[g] is the area of patch g in square kilometers. Used for the density dependence factor f.
        sg : average size of a household per patch. Not used as of yet.


        Other parameters
        ----------------
        Nc : contact matrix between all age groups in stratification

    """

    # ...state variables and parameters

    state_names = ['S', 'E', 'I', 'A', 'M', 'C', 'C_icurec','ICU', 'R', 'D','H_in','H_out','H_tot']
    parameter_names = ['beta_R', 'beta_U', 'beta_M', 'sigma', 'omega', 'zeta','da', 'dm','dhospital', 
                        'dc_R', 'dc_D', 'dICU_R', 'dICU_D', 'dICUrec', 'xi']
    parameters_stratified_names = [['area', 'sg', 'p'], ['s','a','h', 'c', 'm_C','m_ICU']]
    stratification = ['place','Nc'] # mobility and social interaction: name of the dimension (better names: ['nis', 'age'])
    coordinates = ['place'] # 'place' is interpreted as a list of NIS-codes appropriate to the geography
    coordinates.append(None) # age dimension has no coordinates (just integers, which is fine)

    # ..transitions/equations
    @staticmethod

    def integrate(t, S, E, I, A, M, C, C_icurec, ICU, R, D, H_in, H_out, H_tot, # time + SEIRD classes
                  beta_R, beta_U, beta_M, sigma, omega, zeta, da, dm, dhospital, dc_R, dc_D, 
                        dICU_R, dICU_D, dICUrec, xi, # SEIRD parameters
                  area, sg, p,  # spatially stratified parameters. Might delete sg later.
                  s, a, h, c, m_C, m_ICU, # age-stratified parameters
                  place, Nc): # stratified parameters that determine stratification dimensions

        """
        BIOMATH extended SEIRD model for COVID-19
        """

#         print('CHECKPOINT: start integration function')
        
        # calculate total population
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~

        T = S + E + I + A + M + C + C_icurec + ICU + R # calculate total population per age bin using 2D array

        # Define all the parameters needed to determine the rates of change
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        
        # For total population and for the relevant compartments I and A
        G = place.shape[0] # spatial stratification
        N = Nc.shape[0] # age stratification
        
        # Define effective mobility matrix place_eff from user-defined parameter p[patch]
        place_eff = np.outer(p, p)*place + np.identity(G)*np.matmul(place, (1-np.outer(p,p)))
        # TO DO: add age stratification for p
        
        # Effective population per age class per patch: T[patch][age] due to mobility expressed in place and/or regulated by p[patch]
        T_eff = np.matmul(np.transpose(place_eff), T)
        A_eff = np.matmul(np.transpose(place_eff), A)
        I_eff = np.matmul(np.transpose(place_eff), I)
                
        # The number of susceptibles in age class i from patch g that work in patch h. Susc[patch,patch,age]
        Susc = place_eff[:,:,np.newaxis]*S[:,np.newaxis,:]
        
        # Density dependence per patch: f[patch]
        T_eff_total = T_eff.sum(axis=1)
        rho = T_eff_total / area
        f = 1 + (1 - np.exp(-xi * rho))

        # Normalisation factor per age class: zi[age]
        # Population per age class
        Ti = T.sum(axis=0)
        zi = Ti / np.matmul(np.transpose(T_eff),f)
        
        # infer aggregation (prov, arr or mun)
        agg = None
        if G == 11:
            agg = 'prov'
        elif G == 43:
            agg = 'arr'
        elif G == 581:
            agg = 'mun'
        else:
            raise Exception(f"Space is {G}-fold stratified. This is not recognized as being stratification at Belgian province, arrondissement, or municipality level.")
        
        # Define spatially stratified infectivity beta with three degrees of freedom beta_R, beta_U, beta_M, based on stratification
        # Default values for RU_threshold and UM_threshold are taken. beta[patch]
        beta = stratify_beta(beta_R, beta_U, beta_M, agg, area, T.sum(axis=1))
        
        # Define the number of contacts multiplier per patch and age, multip[patch,age]
        multip = np.matmul( (I_eff + A_eff)/T_eff , np.transpose(Nc) )
        
        # Multiply with correctional term for density f[patch], normalisation per age zi[age], and age-dependent susceptibility s[age]
        multip *= np.outer(f, s*zi)
        
        # if infectivity depends on VISITED region (beta^h), beta_localised = True
        # if infectivity depends on region of origin (beta^g), beta_localised = False
        # Change this in hard-code depending on preference
        beta_localised = True
        if beta_localised:
            multip *= beta[:,np.newaxis]
        else:
            Susc *= beta[:,np.newaxis,:]
        
        # So far we have all the interaction happening in the *visited* patch h. We want to know how this affects the people *from* g.
        # We need sum over a patch index h, which is the second index (axis=1). Result is dS_inf[patch,age].
        dS_inf = (Susc * multip[np.newaxis,:,:]).sum(axis=1)
        
        dS  = -dS_inf + zeta*R
        dE  = dS_inf - E/sigma
        dI = (1/sigma)*E - (1/omega)*I
        dA = (a/omega)*I - A/da
        dM = ((1-a)/omega)*I - M*((1-h)/dm) - M*h/dhospital
        dC = M*(h/dhospital)*c - (1-m_C)*C*(1/(dc_R)) - m_C*C*(1/(dc_D))
        dC_icurec = (1-m_ICU)*ICU/(dICU_R) - C_icurec*(1/dICUrec)
        dICUstar = M*(h/dhospital)*(1-c) - (1-m_ICU)*ICU/(dICU_R) - m_ICU*ICU/(dICU_D)
        dR  = A/da + ((1-h)/dm)*M + (1-m_C)*C*(1/dc_R) + C_icurec*(1/dICUrec) - zeta*R
        dD  = (m_ICU/dICU_D)*ICU + (m_C/dc_D)*C
        dH_in = M*(h/dhospital) - H_in
        dH_out =  (1-m_C)*C*(1/dc_R) +  m_C*C*(1/dc_D) + (m_ICU/dICU_D)*ICU + C_icurec*(1/dICUrec) - H_out
        dH_tot = M*(h/dhospital) - (1-m_C)*C*(1/dc_R) -  m_C*C*(1/dc_D) - (m_ICU/dICU_D)*ICU - C_icurec*(1/dICUrec)
        
        return (dS, dE, dI, dA, dM, dC, dC_icurec, dICUstar, dR, dD, dH_in, dH_out, dH_tot)
    
class COVID19_SEIRD_spatial_vacc(BaseModel):
    """
    BIOMATH extended SEIRD model for COVID-19, spatially explicit. Based on COVID_SEIRD and Arenas (2020).

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

        Non-stratified parameters
        -------------------------
        beta_R : probability of infection when encountering an infected person in rural environment
        beta_U : probability of infection when encountering an infected person in urban environment
        beta_M : probability of infection when encountering an infected person in metropolitan environment
        K : infectivity gain of alternative COVID-19 variants (infectivity of new variant = K * infectivity of old variant)
        sigma : length of the latent period
        omega : length of the pre-symptomatic infectious period
        zeta : effect of re-susceptibility and seasonality
        a : probability of an asymptomatic cases
        m : probability of an initially mild infection (m=1-a)
        da : duration of the infection in case of asymptomatic
        dm : duration of the infection in case of mild
        der : duration of stay in emergency room/buffer ward
        dc : average length of a hospital stay when not in ICU
        dICU_R : average length of a hospital stay in ICU in case of recovery
        dICU_D: average length of a hospital stay in ICU in case of death
        dhospital : time before a patient reaches the hospital
        xi : factor controlling the contact dependence on density f
        injection_day : number of days after start of simulation when new strain is injected
        injection_ratio : ratio of new strain vs total amount of virus on injection_day

        Age-stratified parameters
        -------------------------
        s: relative susceptibility to infection
        a : probability of a subclinical infection
        h : probability of hospitalisation for a mild infection
        c : probability of hospitalisation in Cohort (non-ICU)
        m_C : mortality in Cohort
        m_ICU : mortality in ICU
        pi : mobility parameter (1 by default = no measures)
        N_vacc : daily number of people vaccinated in each age group
        e : vaccine effectivity
        leakiness : leakiness of the vaccine (proportion of vaccinated people that contribute to infections)

        Spatially-stratified parameters
        -------------------------------
        place : normalised mobility data. place[g][h] denotes the fraction of the population in patch g that goes to patch h
        area : area[g] is the area of patch g in square kilometers. Used for the density dependence factor f.
        sg : average size of a household per patch. Not used as of yet.


        Other parameters
        ----------------
        Nc : contact matrix between all age groups in stratification

    """

    # ...state variables and parameters

    state_names = ['S', 'E', 'I', 'A', 'M', 'ER', 'C', 'C_icurec','ICU', 'R', 'D','H_in','H_out','H_tot', 'VE', 'V', 'V_new','alpha']
    parameter_names = ['beta_R', 'beta_U', 'beta_M', 'K', 'sigma', 'omega', 'zeta','da', 'dm', 'der','dhospital', 
                        'dc_R', 'dc_D', 'dICU_R', 'dICU_D', 'dICUrec', 'xi', 'injection_day', 'injection_ratio', 'K_inf', 'K_hosp']
    parameters_stratified_names = [['area', 'sg', 'p'], ['s','a','h', 'c', 'm_C','m_ICU', 'v', 'e', 'N_vacc', 'leakiness']]
    stratification = ['place','Nc'] # mobility and social interaction: name of the dimension (better names: ['nis', 'age'])
    coordinates = ['place'] # 'place' is interpreted as a list of NIS-codes appropriate to the geography
    coordinates.append(None) # age dimension has no coordinates (just integers, which is fine)

    # ..transitions/equations
    @staticmethod

    def integrate(t, S, E, I, A, M, ER, C, C_icurec, ICU, R, D, H_in, H_out, H_tot, VE, V, V_new, alpha, # time + SEIRD classes
                  beta_R, beta_U, beta_M, K, sigma, omega, zeta, da, dm, der, dhospital, dc_R, dc_D, 
                        dICU_R, dICU_D, dICUrec, xi, # SEIRD parameters
                  injection_day,  injection_ratio, K_inf, K_hosp, # co-infection params
                  area, sg, p,  # spatially stratified parameters. Might delete sg later.
                  s, a, h, c, m_C, m_ICU, v, e, N_vacc, leakiness, # age-stratified parameters
                  place, Nc): # stratified parameters that determine stratification dimensions

        """
        BIOMATH extended SEIRD model for COVID-19
        """

        # calculate total population
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~

        T = S + E + I + A + M + ER + C + C_icurec + ICU + R + V # calculate total population per age bin using 2D array
        VE = S + R + E + I + A

        # Define all the parameters needed to determine the rates of change
        # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        
        # For total population and for the relevant compartments I and A
        G = place.shape[0] # spatial stratification
        N = Nc.shape[0] # age stratification
        
        # Define effective mobility matrix place_eff from user-defined parameter p[patch]
        place_eff = np.outer(p, p)*place + np.identity(G)*np.matmul(place, (1-np.outer(p,p)))
        # TO DO: add age stratification for p
        
        # Effective population per age class per patch: T[patch][age] due to mobility expressed in place and/or regulated by p[patch]
        T_eff = np.matmul(np.transpose(place_eff), T)
        A_eff = np.matmul(np.transpose(place_eff), A)
        I_eff = np.matmul(np.transpose(place_eff), I)
        V_eff = np.matmul(np.transpose(place_eff), V)
        alpha_eff = np.matmul(np.transpose(place_eff), alpha)
                
        # The number of susceptibles in age class i from patch g that work in patch h. Susc[patch,patch,age]
        Susc = place_eff[:,:,np.newaxis]*S[:,np.newaxis,:]
        V_Susc = place_eff[:,:,np.newaxis]*V[:,np.newaxis,:]
        
        # Density dependence per patch: f[patch]
        T_eff_total = T_eff.sum(axis=1)
        rho = T_eff_total / area
        f = 1 + (1 - np.exp(-xi * rho))

        # Normalisation factor per age class: zi[age]
        # Population per age class
        Ti = T.sum(axis=0)
        zi = Ti / np.matmul(np.transpose(T_eff),f)
        
        # infer aggregation (prov, arr or mun)
        agg = None
        if G == 11:
            agg = 'prov'
        elif G == 43:
            agg = 'arr'
        elif G == 581:
            agg = 'mun'
        else:
            raise Exception(f"Space is {G}-fold stratified. This is not recognized as being stratification at Belgian province, arrondissement, or municipality level.")
        
        # Define spatially stratified infectivity beta with three degrees of freedom beta_R, beta_U, beta_M, based on stratification
        # Default values for RU_threshold and UM_threshold are taken. beta[patch]
        beta = stratify_beta(beta_R, beta_U, beta_M, agg, area, T.sum(axis=1))
        
        # Define actual beta due to VOCs, which is in general age-dependent. beta_weighted_av[patch,age], 
        beta_weighted_av = (1-alpha_eff)*beta[:,np.newaxis] + alpha_eff*K*beta[:,np.newaxis]
        
        # Define the number of contacts multiplier per patch and age, multip[patch,age]
        multip = np.matmul( (I_eff + A_eff + leakiness*V)/T_eff , np.transpose(Nc) )
        
        # Multiply with correctional term for density f[patch], normalisation per age zi[age], and age-dependent susceptibility s[age]
        multip *= np.outer(f, s*zi)
        
        # Multiply with beta_weighted_av[patch,age]
        # if infectivity depends on VISITED region (beta^h), beta_localised = True
        # if infectivity depends on region of origin (beta^g), beta_localised = False
        # Change this in hard-code depending on preference
        beta_localised = True
        if beta_localised:
            multip *= beta_weighted_av
        else:
            Susc *= beta_weighted_av[:,np.newaxis,:]
            V_Susc *= beta_weighted_av[:,np.newaxis,:]
        
        # So far we have all the interaction happening in the *visited* patch h. We want to know how this affects the people *from* g.
        # We need sum over a patch index h, which is the second index (axis=1). Result is dS_inf[patch,age] and dV_inf[patch,age].
        dS_inf = (Susc * multip[np.newaxis,:,:]).sum(axis=1)
        dV_inf = (V_Susc * multip[np.newaxis,:,:]).sum(axis=1)

        dS  = -dS_inf + zeta*R - N_vacc/VE*S
        dE  = dS_inf - E/sigma - N_vacc/VE*E + (1-e)*dV_inf # Unsuccesful vaccinations are added to Exposed population
        dI = (1/sigma)*E - (1/omega)*I - N_vacc/VE*I
        dA = (a/omega)*I - A/da - N_vacc/VE*A
        dM = ((1-a)/omega)*I - M*((1-h)/dm) - M*h/dhospital
        dER = M*(h/dhospital) - (1/der)*ER
        dC = c*(1/der)*ER - (1-m_C)*C*(1/dc_R) - m_C*C*(1/dc_D)
        dC_icurec = ((1-m_ICU)/dICU_R)*ICU - C_icurec*(1/dICUrec)
        dICUstar = (1-c)*(1/der)*ER - (1-m_ICU)*ICU/dICU_R - m_ICU*ICU/dICU_D
        dR  = A/da + ((1-h)/dm)*M + (1-m_C)*C*(1/dc_R) + C_icurec*(1/dICUrec) - zeta*R - N_vacc/VE*R
        dD  = (m_ICU/dICU_D)*ICU + (m_C/dc_D)*C
        dH_in = M*(h/dhospital) - H_in
        dH_out =  (1-m_C)*C*(1/dc_R) +  m_C*C*(1/dc_D) + (m_ICU/dICU_D)*ICU + C_icurec*(1/dICUrec) - H_out
        dH_tot = M*(h/dhospital) - (1-m_C)*C*(1/dc_R) -  m_C*C*(1/dc_D) - (m_ICU/dICU_D)*ICU - C_icurec*(1/dICUrec)
        dV_new = N_vacc/VE*S + N_vacc/VE*R + N_vacc/VE*E + N_vacc/VE*I + N_vacc/VE*A - V_new
        dV = N_vacc/VE*S + N_vacc/VE*R + N_vacc/VE*E + N_vacc/VE*I + N_vacc/VE*A - (1-e)*dV_inf
        dVE = dS + dR + dE + dI + dA
        dalpha = alpha*K/(1-alpha+alpha*K) - alpha
        # If A and I are both zero, a division error occurs
        dalpha[np.isnan(dalpha)] = 0
        



        # On injection_day, inject injection_ratio new strain to alpha (but only if alpha is still zero)
        if (t >= injection_day) & (alpha.sum().sum()==0):
            dalpha += injection_ratio
        

        return (dS, dE, dI, dA, dM, dER, dC, dC_icurec, dICUstar, dR, dD, dH_in, dH_out, dH_tot, dVE, dV_new, dV, dalpha)


#%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
#%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
