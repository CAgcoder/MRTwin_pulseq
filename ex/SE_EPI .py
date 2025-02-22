# %% S0. SETUP env
import MRzeroCore as mr0
import pypulseq as pp
import numpy as np
import torch
from matplotlib import pyplot as plt
import util

# makes the ex folder your working directory
import os 
os.environ['KMP_DUPLICATE_LIB_OK'] = 'True'
os.chdir(os.path.abspath(os.path.dirname(__file__)))


experiment_id = 'spin_echo_EPI_zigzag_withloop'


# %% S1. SETUP sys

# choose the scanner limits
system = pp.Opts(
    max_grad=28, grad_unit='mT/m', max_slew=150, slew_unit='T/m/s',
    rf_ringdown_time=20e-6, rf_dead_time=100e-6,
    adc_dead_time=20e-6, grad_raster_time=50 * 10e-6
)


# %% S2. DEFINE the sequence
seq = pp.Sequence(system = system)

# Define FOV and resolution
fov = 1000e-3
slice_thickness = 8e-3

Nread = 64  # frequency encoding steps/samples
Nphase = 64  # phase encoding steps/samples

# Define rf events
rf1, _, _ = pp.make_sinc_pulse(
    flip_angle=90 * np.pi / 180, duration=1e-3,
    slice_thickness=slice_thickness, apodization=0.5, time_bw_product=4,
    system=system, return_gz=True
) 
rf2, _, _ = pp.make_sinc_pulse(
     flip_angle=180 * np.pi / 180, duration=1e-3,
     slice_thickness=slice_thickness, apodization=0.5, time_bw_product=4,
     system=system, return_gz=True
 )


# Define other gradients and ADC events

gx = pp.make_trapezoid(channel='x', flat_area=Nread , flat_time=0.2e-3, system=system)
gx_ = pp.make_trapezoid(channel='x', flat_area=-Nread , flat_time=0.2e-3, system=system)

gp = pp.make_trapezoid(channel='y', area=1, duration=1e-3, system=system)

gz = pp.make_trapezoid(channel='z', flat_area=Nread, flat_time=0.2e-3, system=system)
gz_ = pp.make_trapezoid(channel='z', flat_area=-Nread/2, flat_time=0.1e-3, system=system)

gx_pre = pp.make_trapezoid(channel='x', area=gx.area / 2, duration=1e-3, system=system)
gy_pre = pp.make_trapezoid(channel='y', area=Nphase / 2, duration=1e-3, system=system)

adc = pp.make_adc(num_samples=Nread, duration=0.2e-3, phase_offset=90* np.pi / 180, delay=gx.rise_time, system=system)


# ======
# CONSTRUCT SEQUENCE
# ======


seq.add_block(rf1,gz)
seq.add_block(gz_,gx_pre,gy_pre)

seq.add_block(pp.make_delay(0.01 - rf1.delay - rf2.delay))
seq.add_block(rf2,gz)
seq.add_block(pp.make_delay(0.005))


for ii in range(0, Nphase//2):  # e.g. -64:63    
    seq.add_block(adc, gx,gp)
    seq.add_block(adc,gx_,gp)
    

# %% S3. CHECK, PLOT and WRITE the sequence  as .seq
# Check whether the timing of the sequence is correct
ok, error_report = seq.check_timing()
if ok:
    print('Timing check passed successfully')
else:
    print('Timing check failed. Error listing follows:')
    [print(e) for e in error_report]

# PLOT sequence
sp_adc, t_adc = mr0.util.pulseq_plot(seq, clear=False, figid=(11,12))

# Prepare the sequence output for the scanner
seq.set_definition('FOV', [fov, fov, slice_thickness])
seq.set_definition('Name', 'gre')
seq.write('out/external.seq')
seq.write('out/' + experiment_id + '.seq')

# %% S4: SETUP SPIN SYSTEM/object on which we can run the MR sequence external.seq fseq.add_block(gp)rom above
sz = [64, 64]

if 1:
    # (i) load a phantom object from file
    # obj_p = mr0.VoxelGridPhantom.load_mat('../data/phantom2D.mat')
    obj_p = mr0.VoxelGridPhantom.load_mat('../data/numerical_brain_cropped.mat')
    obj_p = obj_p.interpolate(sz[0], sz[1], 1)

# Manipulate loaded data
    obj_p.T2dash[:] = 30e-3
    obj_p.D *= 0 
    obj_p.B0 *= 1    # alter the B0 inhomogeneity
    # Store PD for comparison
    PD = obj_p.PD.squeeze()
    B0 = obj_p.B0.squeeze()
else:
    # or (ii) set phantom  manually to a pixel phantom. Coordinate system is [-0.5, 0.5]^3
    obj_p = mr0.CustomVoxelPhantom(
        pos=[[-0.4, -0.4, 0], [-0.4, -0.2, 0], [-0.3, -0.2, 0], [-0.2, -0.2, 0], [-0.1, -0.2, 0]],
        PD=[1.0, 1.0, 0.5, 0.5, 0.5],
        T1=1.0,
        T2=0.1,
        T2dash=0.1,
        D=0.0,
        B0=0,
        voxel_size=0.1,
        voxel_shape="box"
    )
    # Store PD for comparison
    PD = obj_p.generate_PD_map()
    B0 = torch.zeros_like(PD)

obj_p.plot()
obj_p.size=torch.tensor([fov, fov, slice_thickness]) 
# Convert Phantom into simulation data
obj_p = obj_p.build()


# %% S5:. SIMULATE  the external.seq file and add acquired signal to ADC plot

# Read in the sequence
# seq_file = mr0.PulseqFile()
# seq_file.plot()
seq0 = mr0.Sequence.from_seq_file("out/external.seq")
seq0.plot_kspace_trajectory()
kspace_loc = seq0.get_kspace()
# Simulate the sequence
graph = mr0.compute_graph(seq0, obj_p, 200, 1e-3)
signal = mr0.execute_graph(graph, seq0, obj_p,min_emitted_signal=1e-2,min_latent_signal=1e-2)

# PLOT sequence with signal in the ADC subplot
plt.close(11);plt.close(12)
sp_adc, t_adc = mr0.util.pulseq_plot(seq, clear=False, signal=signal.numpy())
 
# additional noise as simulation is perfect


# %% S6: MR IMAGE RECON of signal ::: #####################################
fig = plt.figure()  # fig.clf()
plt.subplot(411)
plt.title('ADC signal')
kspace_adc = torch.reshape((signal), (Nphase, Nread)).clone().t()
plt.plot(torch.real(signal), label='real')
plt.plot(torch.imag(signal), label='imag')

# this adds ticks at the correct position szread
major_ticks = np.arange(0, Nphase * Nread, Nread)
ax = plt.gca()
ax.set_xticks(major_ticks)
ax.grid()

if 0:  # FFT
    # fftshift
    spectrum = torch.fft.fftshift(kspace_adc)
    # FFT
    space = torch.fft.fft2(spectrum)
    # fftshift
    space = torch.fft.fftshift(space)


if 1:  # NUFFT
    import scipy.interpolate
    grid = kspace_loc[:, :2]
    Nx = 64
    Ny = 64

    X, Y = np.meshgrid(np.linspace(0, Nx - 1, Nx) - Nx / 2,
                        np.linspace(0, Ny - 1, Ny) - Ny / 2)
    grid = np.double(grid.numpy())
    grid[np.abs(grid) < 1e-3] = 0

    plt.subplot(347)
    plt.plot(grid[:, 0].ravel(), grid[:, 1].ravel(), 'rx', markersize=3)
    plt.plot(X, Y, 'k.', markersize=2)
    plt.show()

    spectrum_resampled_x = scipy.interpolate.griddata(
        (grid[:, 0].ravel(), grid[:, 1].ravel()),
        np.real(signal.ravel()), (X, Y), method='cubic'
    )
    spectrum_resampled_y = scipy.interpolate.griddata(
        (grid[:, 0].ravel(), grid[:, 1].ravel()),
        np.imag(signal.ravel()), (X, Y), method='cubic'
    )

    kspace_r = spectrum_resampled_x + 1j * spectrum_resampled_y
    kspace_r[np.isnan(kspace_r)] = 0

    # fftshift
    # kspace_r = np.roll(kspace_r,Nx//2,axis=0)
    # kspace_r = np.roll(kspace_r,Ny//2,axis=1)
    kspace_r_shifted = np.fft.fftshift(kspace_r, 0)
    kspace_r_shifted = np.fft.fftshift(kspace_r_shifted, 1)

    space = np.fft.fft2(kspace_r_shifted)
    space = np.fft.fftshift(space, 0)
    space = np.fft.fftshift(space, 1)

space = np.transpose(space)
plt.subplot(345)
plt.title('k-space')
util.MR_imshow(np.abs(kspace_adc))
plt.subplot(349)
plt.title('k-space_r')
util.MR_imshow(np.abs(kspace_r))

plt.subplot(346)
plt.title('FFT-magnitude')
util.MR_imshow(np.abs(space))
plt.colorbar()
plt.subplot(3, 4, 10)
plt.title('FFT-phase')
util.MR_imshow(np.angle(space), vmin=-np.pi, vmax=np.pi)
plt.colorbar()

# % compare with original phantom obj_p.PD
plt.subplot(348)
plt.title('phantom PD')
util.MR_imshow(PD)
plt.subplot(3, 4, 12)
plt.title('phantom B0')
util.MR_imshow(B0)

# # Plot k-spaces
# ktraj_adc, ktraj, t_excitation, t_refocusing, _ = seq.calculate_kspace()
# plt.plot(ktraj.T)  # Plot the entire k-space trajectory
# plt.figure()
# plt.plot(ktraj[0], ktraj[1], 'b')  # 2D plot
# plt.axis('equal')  # Enforce aspect ratio for the correct trajectory display
# plt.plot(ktraj_adc[0], ktraj_adc[1], 'r.')
# plt.show()

