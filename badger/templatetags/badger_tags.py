import json
# django
from django import template
from django.conf import settings
from django.shortcuts import  get_object_or_404
from django.utils.safestring import mark_safe
from django.contrib.auth.models import SiteProfileNotAvailable
from django.core.exceptions import ObjectDoesNotExist
from django.core.urlresolvers import reverse

from settings import BADGER_UPLOADS_URL  
from badger.models import mk_upload_to, Award, Badge, DeferredAward



import hashlib
import urllib

from django.utils.translation import ugettext_lazy as _

register = template.Library()


@register.filter
def permissions_for(obj, user):
    try:
        return obj.get_permissions_for(user)
    except:
        return {}


@register.filter
def key(obj, name):
    try:
        return obj[name]
    except:
        return None


@register.filter
def badge_progress(badge, user):
    return badge.progress_for(user).percent

@register.simple_tag
def user_avatar(user, secure=False, size=256, rating='pg', default=''):

    try:
        profile = user.get_profile()
        if profile.avatar:
            return profile.avatar.url
    except SiteProfileNotAvailable:
        pass
    except ObjectDoesNotExist:
        pass
    except AttributeError:
        pass

    base_url = (secure and 'https://secure.gravatar.com' or
        'http://www.gravatar.com')
    m = hashlib.md5(user.email)
    return '%(base_url)s/avatar/%(hash)s?%(params)s' % dict(
        base_url=base_url, hash=m.hexdigest(),
        params=urllib.urlencode(dict(
            s=size, d=default, r=rating
        ))
    )



@register.simple_tag
def award_image(award):


    if award.image:
        img_url = award.image.url
    elif award.badge.image:
        img_url = award.badge.image.url
    else:
        img_url = "/media/img/default-badge.png"
        
    return img_url
    


    
@register.simple_tag
def user_award_list(badge, user):    


     if badge.allows_award_to(user):
            return '<li><a class="award_badge" href="%s">%s</a></li>' % ( reverse('badger.views.award_badge', args=[badge.slug,]), _('Issue award') )
     else:
        return ''

def badge(badge, size):
    name = mk_upload_to("image.png", size)
    return "%s%s" % (BADGER_UPLOADS_URL, name(badge,"")) 

register.simple_tag(badge)

@register.filter(name="user_award")
def user_award(user):
    awards = Award.objects.filter(user=user)
    return awards

@register.filter
def claim_code_for_badge(badge, user):
    das = DeferredAward.objects.filter(badge=badge, email=user.email)
    if len(das) > 0:
        da = das[0]
        code = da.claim_code
    else:
        code = "" 
    return code
