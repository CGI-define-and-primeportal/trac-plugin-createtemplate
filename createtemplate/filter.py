from trac.core import *
from trac.config import PathOption
from trac.web import ITemplateStreamFilter
from createtemplate.api import ProjectTemplateAPI
from genshi.builder import tag
from genshi.filters.transform import Transformer

# Author: Danny Milsom <danny.milsom@cgi.com>

class Filter(Component):
    """Intercepts requests to the new project ticket type on the 
    dashboard project. We do this so we can dynamically list all project 
    templates."""

    implements(ITemplateStreamFilter)

    # ITemplateStreamFilter

    def filter_stream(self, req, method, filename, stream, data):
        """This is a filter stream for the project request ticket type 
        on the dashboard project. 

        We examine the filter stream and look to find the Project Template
        text input markup. We then replace this with a select list, and generate
        the values using the API get_all_templates() method. 

        We don't have these options in the trac.ini file as they are dynamic, 
        and don't use a select list by default as this will cause validation
        issues."""

        if (filename == 'ticket.html' and data['ticket']['type'] == 'projectrequest'
            and self.env.is_component_enabled('define.dashboard.DashboardDisplayModule')):

            # get a list of available templates
            templates = ProjectTemplateAPI(self.env).get_all_templates()
            # we need a None option for the default
            templates.insert(0, 'None')

            # generate the select list markup
            select = tag.select(name='field_template')
            for template in templates:
                if template == 'None':
                    select.append(tag.option(template, value=template, selected='selected'))
                else:
                    select.append(tag.option(template, value=template))

            # replace the text input with a select list
            stream = stream | Transformer("//*[@id='field-template']").replace(select)

        return stream