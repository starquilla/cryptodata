"""Offline launch decision only. Never deploys, resumes, or calls a provider.

The caller must supply fresh observations; this does not acquire a process lock.
"""
import math


def launch_gate(observation, now):
    reasons = []
    try:
        now = float(now)
        observed = float(observation['observed_at'])
        not_before = float(observation['not_before'])
        deadline = float(observation['deadline'])
        duration = float(observation['max_duration_seconds'])
        next_collect = float(observation['next_pc_collection'])
        times = (now, observed, not_before, deadline, duration, next_collect)
        if not all(math.isfinite(x) for x in times) or duration <= 0:
            raise ValueError('invalid times')
        if not 0 <= now - observed <= 60:
            reasons.append('observations_missing_freshness')
        if now < not_before:
            reasons.append('before_not_before')
        if now + duration + 120 >= min(deadline, next_collect):
            reasons.append('insufficient_window_before_deadline_or_pc_collection')
        cooldowns = observation['cooldowns']
        if not isinstance(cooldowns, dict):
            raise ValueError('cooldowns must be observed explicitly')
        for host, row in cooldowns.items():
            until = float(row['until'])
            if not math.isfinite(until):
                raise ValueError('invalid cooldown')
            if until > now:
                reasons.append('cooldown:' + host)
        for key in ('no_pc_collector', 'no_other_probe', 'no_github_run',
                    'lock_acquired', 'cooldown_observed', 'free_plan',
                    'auto_deploy_off'):
            if observation.get(key) is not True:
                reasons.append('unverified:' + key)
        if observation.get('operation') != 'deploy_reviewed_commit':
            reasons.append('operation_not_reviewed_commit_deploy')
        if observation.get('suspended') is not False:
            reasons.append('suspended_no_safe_deploy_path')
        if observation.get('probe_mode') != 'sample':
            reasons.append('initial_gate_requires_sample')
        target = observation['reviewed_commit']
        if len(target) != 40 or any(c not in '0123456789abcdef' for c in target):
            raise ValueError('invalid commit')
        if observation.get('requested_commit') != target:
            reasons.append('commit_mismatch')
        pause = float(observation['binance_pause_seconds'])
        if not math.isfinite(pause) or pause < 2:
            reasons.append('pacing_below_two_seconds')
    except (KeyError, TypeError, ValueError, OverflowError):
        reasons.append('missing_or_invalid_evidence')
    return {'allowed': not reasons, 'reasons': reasons,
            'scope': 'offline decision; caller still needs live lock and observations'}
