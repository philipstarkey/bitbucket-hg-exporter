'use strict';

// Register `issueDetails` component, along with its associated controller and template
angular.
  module('issueDetails').
  component('issueDetails', {
    templateUrl: 'issue-details/issue-details.template.html',
    controller: ['$http', '$routeParams', '$sce', function IssueDetailsController($http, $routeParams, $sce) {
        var self = this;

        //pagination info
        self.issueId = $routeParams.issueId;
        self.mainHtml = "";
      
        $http.get('bitbucket_data/repositories/philipstarkey/qtutils/issues/'+self.issueId+'.json').then(function(response) {
            self.issue = response.data;
            self.mainHtml = $sce.trustAsHtml(self.issue['content']['html']);
        });
        
    }]
  });