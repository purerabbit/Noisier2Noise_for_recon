import numpy as np
import torch.cuda
import torchvision

from utils import *
from torch.utils.data import DataLoader
from fastmri.models.unet import Unet
from fastmri.models.varnet import VarNet
from zf_data_loader import zf_data

from skimage.metrics import structural_similarity as ssim

from fastmri.evaluate import ssim as ssimfast

import sys
import os
import matplotlib


if len(sys.argv) == 1:
    log_loc = 'logs/cuda/4.0x/ssdu/65370413_2.0'
else:
    all_net_logs = []
    for l in os.walk('logs/cuda/4.0x'):
        if l[0][-1] not in ['u', 'n', 'd', 'x', 'l']:
            all_net_logs.append(l[0])
    for q in os.walk('logs/cuda/8.0x'):
        if q[0][-1] not in ['u', 'n', 'd', 'x', 'l']:
            all_net_logs.append(q[0])
    log_loc = all_net_logs[int(sys.argv[1]) - 1]

    print('Testing network saved in ' + log_loc)

config = load_config(log_loc + '/config')

if 'method' not in config['data']:
    config['data']['method'] = 'n2n'

if 'type' not in config['network']:
    config['network']['type'] = 'unet'

if 'multicoil' not in config['data']:
    config['data']['multicoil'] = False

if 'nx' not in config['data']:
    config['data']['nx'] = 388

if 'ny' not in config['data']:
    config['data']['ny'] = 640

if 'fully_samp_size' not in config['data']:
    config['data']['fully_samp_size'] = 12

if 'poly_order' not in config['data']:
    config['data']['poly_order'] = 8

if 'ncascades' not in config['network']:
    config['network']['ncascades'] = 4

if config['optimizer']['loss'] == 'mse':
    criterion = MSEloss
elif config['optimizer']['loss'] == 'rmse':
    criterion = RMSEloss
elif config['optimizer']['loss'] == 'l1':
    criterion = torch.nn.L1Loss()
elif config['optimizer']['loss'] == 'l1l2':
    criterion = l12loss
else:
    raise NameError('You have chosen an invalid loss')

if torch.cuda.is_available():
    config['network']['device'] = 'cuda'
else:
    config['network']['device'] = 'cpu'
    config['optimizer']['batch_size'] = 1

loss = []
loss_rss = []
all_ssim = []
all_hfen = []
with torch.no_grad():
    multicoil = config['data']['multicoil']
    if config['network']['type'] == 'unet':
        chans = 2 * config['data']['nc'] if multicoil else 2
        base_net = Unet(in_chans=chans, out_chans=chans)
        network = passUnet
    elif config['network']['type'] == 'varnet':
        base_net = VarNet(num_cascades=config['network']['ncascades'])
        print(config['network']['ncascades'])
        network = passVarnet

    sd = 380 #380
    np.random.seed(sd)
    torch.manual_seed(sd)
    torch.cuda.manual_seed_all(sd)

    base_net.load_state_dict(torch.load(log_loc + '/state_dict', map_location=config['network']['device']))
    base_net.to(config['network']['device'])

    # number of parameters in model
    pp = 0
    for p in list(base_net.parameters()):
        nn = 1
        for s in list(p.size()):
            nn = nn * s
        pp += nn
    print('{} parameters in model'.format(pp))

    test_load = DataLoader(zf_data('test', config), batch_size= 2*config['optimizer']['batch_size'], shuffle=True)

    base_net.eval()
    loss = []
    loss_rss = []
    all_ssim = []
    all_hfen = []
    for i, data in enumerate(test_load, 0):
        y0, y, y_tilde, K = data
        y0 = y0.to(config['network']['device'])
        y = y.to(config['network']['device'])
        y_tilde = y_tilde.to(config['network']['device'])
        K = K.to(config['network']['device'])

        pad = torch.abs(y0) < torch.mean(torch.abs(y0)) / 100
        if config['data']['method'] == "n2n":
            outputs = network(y_tilde, base_net)
            y0_est = outputs * (y == 0) / (1 - K) + y
        elif config['data']['method'] in ["ssdu", "ssdu_bern"]:
            outputs = network(y_tilde, base_net)
            y0_est = outputs * (y == 0) + y
        else:
            y0_est = network(y, base_net)

        y0_est[pad] = 0

        x0 = kspaceToRSS(y0)
        x0_est = kspaceToRSS(y0_est)

        for ii in range(x0.shape[0]):
            # mx = np.array(torch.max(x0[ii].detach().cpu()))
            # mn = np.array(torch.min(x0[ii].detach().cpu()))
            x1 = np.array((x0_est[ii, 0]).detach().cpu())
            x2 = np.array((x0[ii, 0]).detach().cpu())
            all_ssim.append(ssim(x1, x2, data_range=x2.max()))
            print(x2.max())
            # all_ssim.append(ssimfast(x1, x2, x2.max() - x2.min()))
            # all_hfen.append(hfen(x1, x2))
            loss.append(nmse(np.asarray(y0_est[ii].detach().cpu()), np.asarray(y0[ii].detach().cpu())))
            loss_rss.append(nmse(x1, x2))

        # if not torch.cuda.is_available():
        #     break

ndisp = 8
if not torch.cuda.is_available():
    ndisp = 2
    log_loc = log_loc + '/cpu_examples'
    if not os.path.isdir(log_loc):
        os.mkdir(log_loc)

f = open(log_loc + "/test_results.txt", 'w')
f.write("model location is " + log_loc + "\n")
f.write("loss: {:e} \n".format(10*np.log10(np.mean(loss).item())))
f.write("RSS loss: {:e} \n".format(10*np.log10(np.mean(loss_rss).item())))
f.write("SSIM: {:e} \n".format(np.mean(all_ssim)))
f.write("HFEN: {:e} \n".format(10*np.log10(np.mean(all_hfen))))
f.write("parameters in model: {:e} \n".format(pp))

f.write("loss on saved examples: ")
for ii in range(ndisp):
    f.write(str(np.round_(loss[-ndisp + ii], 4)) + ",  ")
f.write("\nRSS loss on saved examples: ")
for ii in range(ndisp):
    f.write(str(np.round_(loss_rss[-ndisp + ii], 4)) + ",  ")
f.write("\nSSIM on saved examples: ")
for ii in range(ndisp):
    f.write(str(np.round_(all_ssim[-ndisp + ii], 4)) + ",  ")

np.savez(log_loc + '/results.npz', loss=loss, loss_rss=loss_rss, all_ssim=all_ssim, all_hfen=all_hfen)

def saveIm(im, ndisp, name):
    im = torch.abs(im[0:ndisp])
    im = torchvision.utils.make_grid(im, nrow=4).detach().cpu()
    torchvision.utils.save_image(im, name)

def saveColIm(im, ndisp, name, cmap):
    magma = matplotlib.cm.get_cmap(cmap)
    im = magma(np.abs(im[0, 0].detach().cpu()))
    matplotlib.image.imsave(name, im)

mx = torch.max(torch.abs(x0[0]))
zoomx = 100
zoomy = 80
zoomw = 100
saveIm(x0_est / mx, ndisp, log_loc + '/x0_est.png')
saveIm(x0_est[:, :, zoomx:zoomx+zoomw, zoomy:zoomy+zoomw ] / mx, ndisp, log_loc + '/x0_est_zoom.png')
saveColIm((x0_est - x0)*5 / (mx), ndisp, log_loc + '/x0_est_error.png', 'magma')
saveIm(kspaceToRSS(outputs) / mx, ndisp, log_loc + '/outputs.png')
saveIm((outputs[0:1, 0, 0, 208:432, 48:272] + 1j * outputs[0:1, 1, 0, 208:432, 48:272]) ** 0.2, 1, log_loc + '/outputs_kspace.png')
saveIm((y0_est[0:1, 0, 0, 208:432, 48:272] + 1j * y0_est[0:1, 1, 0, 208:432, 48:272]) ** 0.2, 1, log_loc + '/y0_est.png')
saveIm((y0_est[0:1, 0, 0, 208:432, 48:272] + 1j * y0_est[0:1, 1, 0, 208:432, 48:272] - y0[0:1, 0, 0, 208:432, 48:272] - 1j * y0[0:1, 1, 0, 208:432, 48:272]) ** 0.2, 1, log_loc + '/error_kspace.png')
saveIm(x0 / mx, ndisp, log_loc + '/x0.png')
saveIm(x0[:,:, zoomx:zoomx+zoomw, zoomy:zoomy+zoomw]/ mx, ndisp, log_loc + '/x0_zoom.png')
saveIm(kspaceToRSS(y_tilde) / mx, ndisp, log_loc + '/inputs.png')
saveIm(kspaceToRSS(y) / mx, ndisp, log_loc + '/noisy_target.png')
saveIm(K[0:1, 0, 0, 208:432, 48:272], 1, log_loc + '/K.png')


saveIm((y[0:1, 0, 0, 208:432, 48:272] +1j*y[0:1, 1, 0, 208:432, 48:272])**0.2, 1, log_loc + '/y_mask.png')
saveIm((y_tilde[0:1, 0, 0, 208:432, 48:272] + 1j*y_tilde[0:1, 1, 0, 208:432, 48:272])**0.2, 1, log_loc + '/y_tilde_mask.png')
saveIm((y0[0:1, 0, 0, 208:432, 48:272] + 1j*y0[0:1, 1, 0, 208:432, 48:272])**0.2, 1, log_loc + '/y0.png')
saveIm((y0_est[0:1, 0, 0, 208:432, 48:272] + 1j*y0_est[0:1, 1, 0, 208:432, 48:272])**0.2, 1, log_loc + '/y0_est_sing.png')
saveIm((y0_est[0:1, 0, 0, 208:432, 48:272] + 1j * y0_est[0:1, 1, 0, 208:432, 48:272] - y0[0:1, 0, 0, 208:432, 48:272] - 1j*y0[0:1, 1, 0, 208:432, 48:272])**0.2, 1, log_loc + '/error_kspace_sing.png')
