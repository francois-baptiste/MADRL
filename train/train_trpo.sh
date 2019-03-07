export PYTHONPATH=$(pwd)/..:$(pwd)/../rltools:$(pwd)/../rllab:$PYTHONPATH
python3 ../runners/run_multiaircraft.py rllab \
    --exp_name trpo_full_curr_3_passes_ENV_circle_square\
	--algo tftrpo \
    --step_size 0.01 \
    --discount 0.99 \
    --control decentralized \
    --sampler simple \
    --policy_hidden 100,50,50 \
    --n_iter 1 \
    --batch_size 30000 \
    --max_path_length 1000 \
    --rew_arrival 15.0 \
    --rew_closing 0.05 \
    --rew_nmac -15.0 \
    --rew_large_turnrate -0.05 \
    --rew_large_acc -0.05 \
    --curriculum ../lessons/cas/env.yaml