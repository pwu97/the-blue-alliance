import cloudstorage
import datetime
import json
import logging

from helpers.firebase.firebase_pusher import FirebasePusher
from helpers.match_helper import MatchHelper
from models.event import Event
from models.match import Match
from models.sitevar import Sitevar


class BlueZoneHelper(object):

    TIME_PATTERN = "%Y-%m-%dT%H:%M:%S"
    MAX_TIME_PER_MATCH = datetime.timedelta(minutes=5)
    # BUFFER_AFTER = datetime.timedelta(minutes=4)
    TIME_BUCKET = datetime.timedelta(minutes=1)

    @classmethod
    def get_upcoming_matches(cls, live_events, n=1):
        matches = []
        for event in live_events:
            event_matches = event.matches
            upcoming_matches = MatchHelper.upcomingMatches(event_matches, n)
            matches.extend(upcoming_matches)
        return matches

    @classmethod
    def get_upcoming_match_predictions(cls, live_events):
        predictions = {}
        for event in live_events:
            if event.details and event.details.predictions:
                predictions.update(event.details.predictions['match_predictions'])
        return predictions

    # @classmethod
    # def should_add_match(cls, matches, candidate_match, current_match, predictions, current_timeout):
    #     now = datetime.datetime.now()
    #     if current_match and candidate_match.key_name == current_match.key_name and current_timeout is not None and now > current_timeout:
    #         # We've been on this match for too long, try something else
    #         return None

    #     if candidate_match.predicted_time > now + cls.MAX_TIME_PER_MATCH:
    #         # If this match starts too far in the future, don't include it
    #         return None

    #     # If this match conflicts with the current match, don't bother trying
    #     if current_match and candidate_match.predicted_time <= current_match.predicted_time + cls.BUFFER_AFTER:
    #         return None

    #     # Can we put this match in the beginning of the list?
    #     if not matches or candidate_match.predicted_time + cls.BUFFER_AFTER <= matches[0].predicted_time:
    #         return 0

    #     for i in range(1, len(matches)):
    #         # Can we insert this match in between these two
    #         last_match = matches[i - 1]
    #         next_match = matches[i]
    #         if candidate_match.predicted_time >= last_match.predicted_time + cls.BUFFER_AFTER:
    #             if candidate_match.predicted_time + cls.BUFFER_AFTER <= next_match.predicted_time:
    #                 if candidate_match.key_name in predictions:
    #                     return i

    #     # Can we put this match at the end of the list?
    #     if matches and candidate_match.predicted_time >= matches[-1].predicted_time + cls.BUFFER_AFTER:
    #         return len(matches)

    #     return None

    @classmethod
    def calculate_match_hotness(cls, matches, predictions):
        max_hotness = 0
        min_hotness = float('inf')
        for match in matches:
            if not match.has_been_played and match.key.id() in predictions:
                prediction = predictions[match.key.id()]
                red_score = prediction['red']['score']
                blue_score = prediction['blue']['score']
                if red_score > blue_score:
                    winner_score = red_score
                    loser_score = blue_score
                else:
                    winner_score = blue_score
                    loser_score = red_score

                hotness = winner_score + 2.0*loser_score  # Favor close high scoring matches

                max_hotness = max(max_hotness, hotness)
                min_hotness = min(min_hotness, hotness)
                match.hotness = hotness
            else:
                match.hotness = 0

        for match in matches:
            match.hotness = 100 * (match.hotness - min_hotness) / (max_hotness - min_hotness)

    @classmethod
    def build_fake_event(cls):
        return Event(id='bluezone',
                     name='TBA BlueZone (BETA)',
                     event_short='bluezone',
                     year=datetime.datetime.now().year,
                     webcast_json=json.dumps([{'type': 'twitch', 'channel': 'firstinspires'}]))  # Default to this webcast

    @classmethod
    def update_bluezone(cls, live_events):
        """
        Find the current best match to watch
        Currently favors showing something over nothing, is okay with switching
        TO a feed in the middle of a match, but avoids switching FROM a feed
        in the middle of a match.
        1. Get the earliest predicted unplayed match across all live events
        2. Get all matches that start within TIME_BUCKET of that match
        3. Switch to hottest match in that bucket unless MAX_TIME_PER_MATCH is
        hit (in which case blacklist for the future)
        4. Repeat
        """
        now = datetime.datetime.now()
        logging.info("[BLUEZONE] Current time: {}".format(now))
        to_log = '--------------------------------------------------\n'
        to_log += "[BLUEZONE] Current time: {}\n".format(now)

        bluezone_config = Sitevar.get_or_insert('bluezone')
        logging.info("[BLUEZONE] Config: {}".format(bluezone_config.contents))
        to_log += "[BLUEZONE] Config: {}\n".format(bluezone_config.contents)
        current_match_key = bluezone_config.contents.get('current_match')
        current_match_added_time = bluezone_config.contents.get('current_match_added')
        if current_match_added_time:
            current_match_added_time = datetime.datetime.strptime(current_match_added_time, cls.TIME_PATTERN)
        blacklisted_match_keys = bluezone_config.contents.get('blacklisted_matches', set())
        if blacklisted_match_keys:
            blacklisted_match_keys = set(blacklisted_match_keys)

        current_match = Match.get_by_id(current_match_key) if current_match_key else None
        upcoming_matches = cls.get_upcoming_matches(live_events)
        upcoming_matches = filter(lambda m: m.predicted_time is not None, upcoming_matches)
        upcoming_predictions = cls.get_upcoming_match_predictions(live_events)

        # (1, 2) Find earliest predicted unplayed match and all other matches
        # that start within TIME_BUCKET of that match
        upcoming_matches.sort(key=lambda match: match.predicted_time)
        potential_matches = []
        time_cutoff = None
        for match in upcoming_matches:
            if match.predicted_time:
                if time_cutoff is None:
                    time_cutoff = match.predicted_time + cls.TIME_BUCKET
                    potential_matches.append(match)
                elif match.predicted_time < time_cutoff:
                    potential_matches.append(match)
                else:
                    break  # Matches are sorted by predicted_time
        logging.info("[BLUEZONE] potential_matches sorted by predicted time: {}".format([pm.key.id() for pm in potential_matches]))
        to_log += "[BLUEZONE] potential_matches sorted by predicted time: {}\n".format([pm.key.id() for pm in potential_matches])

        # (3) Choose hottest match that's not blacklisted
        cls.calculate_match_hotness(potential_matches, upcoming_predictions)
        potential_matches.sort(key=lambda match: -match.hotness)
        logging.info("[BLUEZONE] potential_matches sorted by hotness: {}".format([pm.key.id() for pm in potential_matches]))
        to_log += "[BLUEZONE] potential_matches sorted by hotness: {}\n".format([pm.key.id() for pm in potential_matches])

        bluezone_match = None
        new_blacklisted_match_keys = set()
        for match in potential_matches:
            logging.info("[BLUEZONE] Trying potential match: {}".format(match.key.id()))
            to_log += "[BLUEZONE] Trying potential match: {}\n".format(match.key.id())
            if match.key.id() not in blacklisted_match_keys:
                if match.key.id() == current_match_key:
                    if current_match_added_time + cls.MAX_TIME_PER_MATCH < now:
                        # We've been on this match too long
                        new_blacklisted_match_keys.add(match.key.id())
                        logging.info("[BLUEZONE] Adding match to blacklist: {}".format(match.key.id()))
                        to_log += "[BLUEZONE] Adding match to blacklist: {}\n".format(match.key.id())
                        logging.info("[BLUEZONE] added time: {}, now: {}".format(current_match_added_time, now))
                        to_log += "[BLUEZONE] added time: {}, now: {}\n".format(current_match_added_time, now)
                    else:
                        # We can continue to use this match
                        bluezone_match = match
                        logging.info("[BLUEZONE] Continuing to use match: {}".format(match.key.id()))
                        to_log += "[BLUEZONE] Continuing to use match: {}\n".format(match.key.id())
                else:
                    # Found a new good match
                    bluezone_match = match
                    logging.info("[BLUEZONE] Found a good new match: {}".format(match.key.id()))
                    to_log += "[BLUEZONE] Found a good new match: {}\n".format(match.key.id())
                    break
            else:
                logging.info("[BLUEZONE] Match already blacklisted: {}".format(match.key.id()))
                to_log += "[BLUEZONE] Match already blacklisted: {}\n".format(match.key.id())
                new_blacklisted_match_keys.add(match.key.id())

        if not bluezone_match:
            logging.info("[BLUEZONE] No match selected")
            to_log += "[BLUEZONE] No match selected\n"

        # (3) Switch to hottest match
        fake_event = cls.build_fake_event()
        if bluezone_match and bluezone_match.key_name != current_match_key:
            real_event = filter(lambda x: x.key_name == bluezone_match.event_key_name, live_events)[0]
            real_event_webcasts = real_event.current_webcasts
            if real_event_webcasts:
                fake_event.webcast_json = json.dumps([real_event_webcasts[0]])
                FirebasePusher.update_event(fake_event)
                bluezone_config.contents = {
                    'current_match': bluezone_match.key.id(),
                    'current_match_added': now.strftime(cls.TIME_PATTERN),
                    'blacklisted_matches': list(new_blacklisted_match_keys),
                }
                bluezone_config.put()

                logging.info("[BLUEZONE] Switching to: {}".format(bluezone_match.key.id()))
                to_log += "[BLUEZONE] Switching to: {}\n".format(bluezone_match.key.id())

                # Log to cloudstorage
                log_dir = '/tbatv-prod-hrd.appspot.com/tba-logging/'
                log_file = 'bluezone_{}.txt'.format(now.date())

                existing_contents = ''
                if log_file in set(cloudstorage.listbucket(log_dir)):
                    with cloudstorage.open(log_dir + log_file, 'r') as existing_file:
                        existing_contents = existing_file.read()

                with cloudstorage.open(log_dir + log_file, 'w') as new_file:
                    new_file.write(existing_contents + to_log)

        if bluezone_match:
            FirebasePusher.replace_event_matches('bluezone', [bluezone_match])

        return fake_event