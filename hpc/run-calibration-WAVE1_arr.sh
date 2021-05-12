#!/bin/bash
#PBS -N calibration-COVID19-SEIRD-WAVE1_arr ## job name
#PBS -l nodes=1:ppn=36 ## single-node job, on 36 cores
#PBS -l walltime=72:00:00 ## max. 72h of wall time. This is OK because the node has been reserved anyway

#####################################################
# Choose initial condition
init=BXL

# Choose number of PSO and MCMC steps
maxiter=50
number=100

# Choose name of output files
signature=${init}_${maxiter}xPSO_${number}xMCMC_arr
echo Initiating job with signature \"${signature}\"
#####################################################

# Switch to the reserved cluster
module swap cluster/skitty

# Change to package folder (probably superfluous)
cd $VSC_HOME/Documents/COVID19-Model/hpc/

# Make script executable
# chmod +x calibrate-COVID-19-SEIRD-WAVE1_arr.py

# Activate conda environment
source activate COVID_MODEL

# Execute script with correct parsed arguments
python mrollier-calibration-WAVE1_arr.py -i ${init} -m ${maxiter} -n ${number} -s ${signature}

# Deactivate environment
conda deactivate