from consts.district_type import DistrictType
from consts.notification_type import NotificationType
from notifications.base_notification import BaseNotification


class DistrictPointsUpdatedNotification(BaseNotification):

    # disrict_key is like <year><enum>
    # Example: 2014ne
    def __init__(self, district_key):
        self.district_key = district_key
        self.district_enum = DistrictType.abbrevs[district_key[4:]]

    def _build_dict(self):
        data = {}
        data['message_type'] = NotificationType.type_names[NotificationType.DISTRICT_POINTS_UPDATED]
        data['message_data'] = {}
        data['message_data']['district_key'] = self.district_key
        data['message_data']['district_name'] =  DistrictType.names[self.district_enum]
        return data
